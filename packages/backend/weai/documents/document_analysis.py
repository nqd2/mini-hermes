"""Read-only, revision-scoped survey data for the document analysis worker."""

from collections import Counter
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import posixpath
import re
from zipfile import ZipFile

FORMATS = {'.xlsx', '.pdf', '.docx', '.pptx'}
MAX_UNITS = 5000
MAX_PIXELS = 8_000_000
MAX_IMAGES = 200
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024


def wants_document_summary(prompt: str) -> bool:
    return bool(re.search(r'\b(?:summari[sz]e|summary)\b|tóm\s+tắt', prompt, re.I)) and not re.search(
        r'\b(?:pages?|slides?|sheet|rows?|sections?|chapters?)\s+(?:\d|["“])'
        r'|\b(?:first|second|third|last|\d+(?:st|nd|rd|th))\s+(?:page|slide|sheet|row|section|chapter)\b'
        r'|\b(?:sheet|worksheet|tab|chapter|section)\s+(?!(?:in|of|and|for|from|is|all|này)\b)\w+'
        r'|\b(?:trang|slide|dòng)\s+(?:đầu(?:\s+tiên)?|cuối|thứ\s+\w+)'
        r'|(?:trang|dòng|mục|chương)\s+(?:số\s+)?\d|\b[A-Z]{1,3}\d+:[A-Z]{1,3}\d+\b',
        prompt, re.I,
    )


@dataclass
class Survey:
    profile: dict = field(default_factory=dict)
    units: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    images: int = 0
    report: Callable[[str], None] | None = field(default=None, repr=False)

    def add(self, kind, locator, text):
        text = text.strip()
        for offset in range(0, len(text), 2400):
            self.append({'kind': kind, 'locator': locator, 'offset': offset, 'text': text[offset:offset + 2400]})

    def append(self, unit):
        if len(self.units) < MAX_UNITS:
            self.units.append(unit)
        elif not any(gap['reason'] == 'unit_limit' for gap in self.gaps):
            self.gaps.append({'locator': unit['locator'], 'reason': 'unit_limit'})

    def image(self, locator, *, asset=None, page=None):
        if self.images >= MAX_IMAGES:
            if not any(gap['reason'] == 'ocr_limit' for gap in self.gaps):
                self.gaps.append({'locator': locator, 'reason': 'ocr_limit'})
            return
        self.images += 1
        self.append({'kind': 'ocr', 'locator': locator, 'asset': asset, 'page': page, 'text': ''})


def _display(cell):
    value = cell.value
    if value is None:
        return ''
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        number = int(value)
        if number >= 0 and re.fullmatch(r'0{2,}', cell.number_format or ''):
            return str(number).zfill(len(cell.number_format))
    return ' '.join(str(value).split())


_COLUMNS = {
    'key': ('program code', '专业代码', 'record id', 'product code', 'mã chương trình', 'sku'),
    'name': ('programs', 'program name', '专业名称', 'name', 'tên chương trình', 'product'),
    'language': ('teaching language', '授课语言', 'ngôn ngữ giảng dạy'),
    'duration': ('duration', '学制', 'thời lượng'),
    'degree_type': ('degree type', '学位类型'),
    'enrollment': ('enrollment', '计划招生'),
    'acceptance_letter': ('acceptance letter', '接收函'),
    'scholarship_visitors': ('csc visiting', '中国政府奖学金'),
    'exchange_students': ('exchanging students', '校际交流'),
    'other_visitors': ('other types of non-degree visiting', '其他类型非学历进修'),
}


def _declared_tables(sheet):
    from openpyxl.utils.cell import range_boundaries

    result = []
    for table in list(sheet.tables.values())[:128]:
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        start = min_row + int(table.headerRowCount or 0)
        end = max_row - int(table.totalsRowCount or 0)
        counters = [Counter() for _ in range(min(32, max_col - min_col + 1))]
        records = 0
        if start <= end:
            for row in sheet.iter_rows(min_row=start, max_row=end, min_col=min_col, max_col=max_col):
                labels = [_display(cell) for cell in row]
                if any(labels):
                    records += 1
                    for counter, label in zip(counters, labels):
                        counter[label] += 1
        result.append({
            'name': table.displayName, 'range': table.ref, 'records': records,
            'columns': [
                {'name': column.name, 'missing': counter.get('', 0), 'distinct': len(counter) - int('' in counter),
                 'counts': {key[:120]: count for key, count in counter.most_common(20) if key}}
                for column, counter in zip(table.tableColumns, counters)
            ],
            'column_count': max_col - min_col + 1,
        })
    return result


_REL_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _package_relationships(archive, path):
    from defusedxml.ElementTree import fromstring

    parent, name = posixpath.split(path)
    relationship_path = posixpath.join(parent, '_rels', name + '.rels')
    if relationship_path not in archive.namelist():
        return {}
    return {
        relation.get('Id'): posixpath.normpath(
            target.lstrip('/') if target.startswith('/') else posixpath.join(parent, target)
        )
        for relation in fromstring(archive.read(relationship_path))
        if relation.get('TargetMode') != 'External' and (target := relation.get('Target'))
    }


def _workbook_objects(survey, raw):
    from defusedxml.ElementTree import fromstring
    from openpyxl.utils import get_column_letter

    xdr = '{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}'
    xml_ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    drawing_ns = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    chart_ns = '{http://schemas.openxmlformats.org/drawingml/2006/chart}'
    with ZipFile(BytesIO(raw)) as archive:
        paths = set(archive.namelist())
        manifest = 'xl/workbook.xml'
        if manifest not in paths:
            survey.gaps.append({'locator': 'workbook drawings', 'reason': 'drawing_locations_unavailable'})
            return
        sheets = _package_relationships(archive, manifest)
        for sheet in fromstring(archive.read(manifest)).iter(xml_ns + 'sheet'):
            sheet_path = sheets.get(sheet.get(_REL_ID + 'id'))
            if sheet_path not in paths:
                continue
            title = sheet.get('name')
            drawings = _package_relationships(archive, sheet_path)
            for drawing in sorted(set(drawings.values()) & paths):
                if not drawing.startswith('xl/drawings/') or not drawing.endswith('.xml'):
                    continue
                assets = _package_relationships(archive, drawing)
                for i, anchor in enumerate(fromstring(archive.read(drawing)), 1):
                    start = anchor.find(xdr + 'from')
                    location = f'{title}/drawing/{i}'
                    if start is not None:
                        col, row = start.findtext(xdr + 'col'), start.findtext(xdr + 'row')
                        if col is not None and row is not None:
                            try:
                                location = f'{title}!{get_column_letter(int(col) + 1)}{int(row) + 1}/drawing/{i}'
                            except ValueError:
                                pass
                    for blip in anchor.iter(drawing_ns + 'blip'):
                        asset = assets.get(blip.get(_REL_ID + 'embed'))
                        if asset in paths and asset.startswith('xl/media/'):
                            survey.image(location, asset=asset)
                        else:
                            survey.gaps.append({'locator': location, 'reason': 'external_or_missing_image'})
                    for chart in anchor.iter(chart_ns + 'chart'):
                        asset = assets.get(chart.get(_REL_ID + 'id'))
                        if asset in paths and asset.startswith('xl/charts/'):
                            _chart_xml(survey, location, archive.read(asset))
                        else:
                            survey.gaps.append({'locator': location, 'reason': 'chart_values_unavailable'})


def _workbook(survey, raw):
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(raw), keep_links=False)
    values = load_workbook(BytesIO(raw), data_only=True, read_only=True, keep_links=False)
    try:
        for sheet in workbook.worksheets:
            locator = f"'{sheet.title.replace(chr(39), chr(39) * 2)}'"
            survey.profile.setdefault('sheets', []).append(profile := {
                'name': sheet.title, 'visibility': sheet.sheet_state, 'dimensions': sheet.calculate_dimension(),
                'max_row': sheet.max_row, 'max_column': sheet.max_column,
                'merged_ranges': sorted(str(r) for r in sheet.merged_cells.ranges)[:1000],
                'merged_range_count': len(sheet.merged_cells.ranges),
            })
            if sheet.max_row * sheet.max_column > 1_000_000:
                survey.gaps.append({'locator': locator, 'reason': 'cell_limit'})
                continue
            layout = {}
            keys = Counter()
            counts = {}
            record_rows = departments = nonempty = formulas = missing_cache = 0
            record_kind = 'record'
            bounds = [None, None, 0, 0]
            needs_header = None
            block = []
            block_start, block_end = 0, 0
            cached_rows = values[sheet.title].iter_rows(
                min_row=1, min_col=1, max_row=sheet.max_row, max_col=sheet.max_column, values_only=True,
            )
            for row, cached in zip(sheet.iter_rows(), cached_rows, strict=True):
                labels = [_display(cell) for cell in row]
                if survey.report and row[0].row % 500 == 0:
                    survey.report(f'Scanned {row[0].row}/{sheet.max_row} worksheet rows.\n')
                if not any(labels):
                    continue
                nonempty += 1
                for cell, label in zip(row, labels):
                    if not label:
                        continue
                    bounds[0] = min(bounds[0] or cell.row, cell.row)
                    bounds[1] = min(bounds[1] or cell.column, cell.column)
                    bounds[2] = max(bounds[2], cell.row)
                    bounds[3] = max(bounds[3], cell.column)
                candidate = {
                    key: next((i for i, label in enumerate(labels)
                               if any(term in label.casefold() for term in terms)), None)
                    for key, terms in _COLUMNS.items()
                }
                if candidate['key'] is not None and candidate['name'] is not None:
                    layout = {key: i for key, i in candidate.items() if i is not None}
                    needs_header = f'{locator}!{row[0].row}'
                    key_label = labels[layout['key']].casefold()
                    if any(term in key_label for term in ('program code', '专业代码', 'mã chương trình')):
                        record_kind = 'program'
                    for key in layout.keys() - {'name', 'key'}:
                        counts.setdefault(key, Counter())
                elif layout:
                    key = labels[layout['key']]
                    if re.fullmatch(r'[A-Za-z0-9._-]+', key) and labels[layout['name']]:
                        keys[key] += 1
                        record_rows += 1
                        for column in layout.keys() - {'name', 'key'}:
                            counts[column][labels[layout[column]]] += 1
                first = labels[0].casefold()
                if ('学院' in first and '名称' in first) or ('school' in first and 'name' in first):
                    departments += bool(len(labels) > 1 and labels[1])
                cells = []
                for cell, label, cached_value in zip(row, labels, cached):
                    if not label:
                        continue
                    if cell.data_type == 'f':
                        formulas += 1
                        missing_cache += cached_value is None
                        label += f' [cached: {cached_value}]' if cached_value is not None else ' [cache missing]'
                    cells.append(f'{cell.coordinate}={label}')
                if block and sum(len(line) for line in block) >= 1600:
                    survey.add('rows', f'{locator}!{block_start}:{block_end}', '\n'.join(block))
                    block = []
                if not block:
                    block_start = row[0].row
                block_end = row[0].row
                block.append(' | '.join(cells))
            if block:
                survey.add('rows', f'{locator}!{block_start}:{block_end}', '\n'.join(block))
            profile.update({
                'occupied_bounds': bounds if nonempty else None, 'nonempty_rows': nonempty,
                'formulas': {'count': formulas, 'missing_cache': missing_cache},
                'tables': _declared_tables(sheet),
            })
            if layout:
                profile.update({
                    'header_locator': needs_header, 'records': record_rows,
                    'record_kind': record_kind, 'unique_keys': len(keys),
                    'department_sections': departments,
                    'columns': {label: {'counts': dict((key[:120], n) for key, n in counter.most_common(20) if key),
                                        'missing': record_rows - sum(n for key, n in counter.items() if key),
                                        'distinct': len(counter) - int('' in counter)}
                                for label, counter in counts.items()},
                })
            elif nonempty:
                profile['review_locator'] = locator
    finally:
        workbook.close()
        values.close()
    _workbook_objects(survey, raw)


def _image_parts(survey, element, rels, locator):
    rel_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    tags = {'{http://schemas.openxmlformats.org/drawingml/2006/main}blip', '{urn:schemas-microsoft-com:vml}imagedata'}
    for index, blip in enumerate((item for item in element.iter() if item.tag in tags), start=1):
        relation = blip.get(rel_ns + 'embed') or blip.get(rel_ns + 'id')
        part = rels.get(relation)
        if part is not None and not part.is_external:
            survey.image(f'{locator}/image/{index}', asset=str(part.target_part.partname).lstrip('/'))
        else:
            survey.gaps.append({'locator': f'{locator}/image/{index}', 'reason': 'external_image'})


def _word(survey, raw):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(BytesIO(raw))
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    for index, child in enumerate(document.element.body, 1):
        locator = f'block/{index}'
        if child.tag.endswith('}p'):
            paragraph = Paragraph(child, document)
            style = paragraph.style.name if paragraph.style else ''
            survey.add('heading' if style.casefold().startswith('heading') else 'paragraph', locator, paragraph.text)
        elif child.tag.endswith('}tbl'):
            table = Table(child, document)
            for row, item in enumerate(table.rows, 1):
                survey.add('table', f'{locator}/row/{row}', ' | '.join(cell.text for cell in item.cells))
        elif not child.tag.endswith('}sectPr'):
            survey.gaps.append({'locator': locator, 'reason': 'unsupported_word_block'})
        for box_index, box in enumerate(child.iter(w + 'txbxContent'), 1):
            text = '\n'.join(node.text or '' for node in box.iter(w + 't'))
            if text.strip():
                survey.add('text_box', f'{locator}/textbox/{box_index}', text)
            else:
                survey.gaps.append({'locator': f'{locator}/textbox/{box_index}', 'reason': 'unreadable_text_box'})
        _image_parts(survey, child, document.part.rels, locator)
        for chart_index, chart in enumerate(child.iter('{http://schemas.openxmlformats.org/drawingml/2006/chart}chart'), 1):
            rel = document.part.rels.get(chart.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'))
            if rel and not rel.is_external:
                _chart_xml(survey, f'{locator}/chart/{chart_index}', rel.target_part.blob)
            else:
                survey.gaps.append({'locator': f'{locator}/chart/{chart_index}', 'reason': 'chart_values_unavailable'})
    for relation in document.part.rels.values():
        if relation.reltype.rsplit('/', 1)[-1] not in {'header', 'footer', 'footnotes', 'endnotes', 'comments'}:
            continue
        if not relation.is_external:
            part = relation.target_part
            locator = str(part.partname).lstrip('/')
            if hasattr(part, 'element'):
                survey.add('supplement', locator, '\n'.join(node.text or '' for node in part.element.iter(w + 't')))
                _image_parts(survey, part.element, part.rels, locator)
            else:
                survey.gaps.append({'locator': locator, 'reason': 'unsupported_word_part'})


def _chart_xml(survey, locator, xml):
    from defusedxml.ElementTree import fromstring

    ns = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    chart = fromstring(xml)
    series = []
    for item in chart.findall('.//c:ser', ns):
        values = {}
        for field in ('tx', 'cat', 'val', 'xVal', 'yVal', 'bubbleSize'):
            node = item.find(f'c:{field}', ns)
            if node is not None:
                values[field] = [v.text for v in node.findall('.//c:v', ns)]
        if values:
            series.append(values)
    labels = [label.text for label in chart.findall('.//c:title//a:t', ns)]
    if series or labels:
        from json import dumps
        survey.add('chart', locator, dumps({'labels': labels, 'series': series}, ensure_ascii=False))
    if not any(s.get('val') or s.get('yVal') for s in series):
        survey.gaps.append({'locator': locator, 'reason': 'chart_values_unavailable'})


def _slides(survey, raw):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    deck = Presentation(BytesIO(raw))
    survey.profile['slides'] = len(deck.slides)

    def read_shapes(shapes, prefix, rels):
        for index, shape in enumerate(shapes, 1):
            locator = f'{prefix}/shape/{index}'
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                read_shapes(shape.shapes, locator, rels)
                continue
            if shape.has_text_frame:
                survey.add('text', locator, shape.text)
            if shape.has_table:
                for row, item in enumerate(shape.table.rows, 1):
                    survey.add('table', f'{locator}/row/{row}', ' | '.join(cell.text for cell in item.cells))
            if shape.has_chart:
                _chart_xml(survey, locator, shape.chart.part.blob)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                _image_parts(survey, shape._element, rels, locator)

    for index, slide in enumerate(deck.slides, 1):
        read_shapes(slide.shapes, f'slide/{index}', slide.part.rels)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            survey.add('notes', f'slide/{index}/notes', slide.notes_slide.notes_text_frame.text)


def _pdf(survey, raw):
    from pypdf import PdfReader

    document = PdfReader(BytesIO(raw))
    survey.profile['pages'] = len(document.pages)
    for index, page in enumerate(document.pages, 1):
        locator = f'page/{index}'
        try:
            text = page.extract_text() or ''
            survey.add('text', locator, text)
            if not text.strip():
                survey.image(locator, page=index)
            elif page.images:
                survey.image(locator + '/images', page=index)
        except Exception:
            survey.gaps.append({'locator': locator, 'reason': 'page_extraction_failed'})


def survey_parser(filename: str):
    return {'.xlsx': _workbook, '.pdf': _pdf, '.docx': _word, '.pptx': _slides}[Path(filename).suffix.lower()]


def survey_document(raw: bytes, filename: str, *, report=None) -> Survey:
    suffix = Path(filename).suffix.lower()
    parser = survey_parser(filename)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError('Document exceeds the analysis size limit')
    if suffix != '.pdf':
        with ZipFile(BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_ARCHIVE_BYTES:
                raise ValueError('Document exceeds the expanded analysis size limit')
    survey = Survey(profile={'format': suffix[1:]}, report=report)
    parser(survey, raw)
    return survey


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR
    from weai.env import RAPIDOCR_PARAMS

    return RapidOCR(params=RAPIDOCR_PARAMS)


def ocr_unit(raw: bytes, unit: dict) -> tuple[str, list[float]]:
    from PIL import Image
    import numpy as np

    if unit.get('page'):
        from pypdfium2 import PdfDocument

        with PdfDocument(raw) as document:
            with closing(document[unit['page'] - 1]) as page:
                width, height = page.get_size()
                scale = min(2.0, (MAX_PIXELS / max(1, width * height)) ** 0.5)
                with closing(page.render(scale=scale)) as bitmap:
                    pixels = np.array(bitmap.to_pil().convert('RGB'))
    else:
        with ZipFile(BytesIO(raw)) as archive:
            asset = archive.getinfo(unit['asset'])
            if asset.file_size > 16 * 1024 * 1024:
                raise ValueError('OCR image exceeds the byte limit')
            with Image.open(BytesIO(archive.read(asset))) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('OCR image exceeds the pixel limit')
                pixels = np.array(image.convert('RGB'))
    output = _ocr_engine()(pixels)
    texts = output.txts if output and output.txts is not None else ()
    scores = output.scores if output and output.scores is not None else ()
    return '\n'.join(texts), [float(score) for score in scores]
