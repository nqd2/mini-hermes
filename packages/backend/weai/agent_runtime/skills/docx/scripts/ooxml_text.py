"""Extract and re-inject translatable/editable text in DOCX OOXML folders.

Preserves styles, inline bold/italic runs, tables, headers, footers, footnotes,
endnotes, comments, SDTs (Table of Contents), textboxes, drawings/images, and
Word field instructions (w:instrText / w:fldChar).

Usage:
    python ooxml_text.py extract <input.docx> <work_dir> [--chunk-chars 9000]
    python ooxml_text.py apply <work_dir> <translated_dir_or_json> <output.docx>
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from merge_runs import merge_runs  # noqa: E402
from office.helpers import rezip, safe_extract  # noqa: E402

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
W = f'{{{W_NS}}}'
XML_SPACE_ATTR = f'{{{XML_NS}}}space'

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
_SEGMENT_TAG_RE = re.compile(r'<s(\d+)>(.*?)</s\1>', re.DOTALL)
_ANY_SEGMENT_TAG_RE = re.compile(r'</?s\d+>')
_FENCE_RE = re.compile(r'^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$', re.DOTALL | re.IGNORECASE)

_PROTECTED_RUN_CHILDREN = frozenset(
    {
        f'{W}drawing',
        f'{W}pict',
        f'{W}object',
        f'{W}fldChar',
        f'{W}instrText',
        f'{W}delText',
        f'{W}footnoteReference',
        f'{W}endnoteReference',
        f'{W}commentReference',
        f'{W}sym',
    }
)
_BREAK_RUN_CHILDREN = frozenset({f'{W}tab', f'{W}br', f'{W}cr'})
_IGNORED_RPR_CHILDREN = frozenset({f'{W}lang', f'{W}noProof', f'{W}rPrChange'})


def _part_sort_key(rel: str) -> tuple[int, str]:
    if rel == 'word/document.xml':
        return (0, rel)
    if rel.startswith('word/header'):
        return (1, rel)
    if rel.startswith('word/footer'):
        return (2, rel)
    if rel == 'word/footnotes.xml':
        return (3, rel)
    if rel == 'word/endnotes.xml':
        return (4, rel)
    if rel == 'word/comments.xml':
        return (5, rel)
    return (9, rel)


def _discover_xml_parts(unpacked: Path) -> list[str]:
    word_dir = unpacked / 'word'
    if not word_dir.is_dir():
        return []
    parts: list[str] = []
    for path in word_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != '.xml':
            continue
        name = path.name
        if (
            name == 'document.xml'
            or name.startswith('header')
            or name.startswith('footer')
            or name in {'footnotes.xml', 'endnotes.xml', 'comments.xml'}
        ):
            parts.append(f'word/{name}')
    return sorted(parts, key=_part_sort_key)


def _normalized_rpr(run_el: etree._Element) -> bytes:
    rpr = run_el.find(f'{W}rPr')
    if rpr is None:
        return b''
    cloned = copy.deepcopy(rpr)
    for key in list(cloned.attrib):
        if 'rsid' in key.lower():
            del cloned.attrib[key]
    for child in list(cloned):
        if child.tag in _IGNORED_RPR_CHILDREN:
            cloned.remove(child)
    if len(cloned) == 0 and not cloned.attrib:
        return b''
    return etree.tostring(cloned, encoding='utf-8')


def _paragraph_style(p_el: etree._Element) -> str:
    ppr = p_el.find(f'{W}pPr')
    if ppr is None:
        return ''
    pstyle = ppr.find(f'{W}pStyle')
    if pstyle is None:
        return ''
    return pstyle.get(f'{W}val') or ''


def _paragraph_context(p_el: etree._Element) -> dict[str, bool]:
    in_sdt = False
    in_table = False
    in_textbox = False
    parent = p_el.getparent()
    while parent is not None:
        tag = parent.tag
        if tag == f'{W}sdt':
            in_sdt = True
        elif tag == f'{W}tbl':
            in_table = True
        elif tag == f'{W}txbxContent':
            in_textbox = True
        parent = parent.getparent()
    return {'in_sdt': in_sdt, 'in_table': in_table, 'in_textbox': in_textbox}


def _iter_own_run_nodes(p_el: etree._Element):
    """Yield <w:r> elements belonging to p_el without descending into nested <w:p> or <w:del>."""

    def walk(node: etree._Element):
        for child in node:
            tag = child.tag
            if tag == f'{W}p' or tag == f'{W}del' or tag == f'{W}pPr':
                continue
            if tag == f'{W}r':
                yield child
                # Inspect inside drawing textboxes separately as their own <w:p> elements.
                continue
            yield from walk(child)

    yield from walk(p_el)


def _extract_paragraph_groups(p_el: etree._Element) -> list[dict[str, Any]]:
    """Return contiguous editable run groups within p_el, walking run children in order."""
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field_instr_stack: list[bool] = []
    after_break = False

    def flush() -> None:
        nonlocal current
        if current is not None and current['t_elements']:
            groups.append(current)
        current = None

    for run_el in _iter_own_run_nodes(p_el):
        parent = run_el.getparent()
        sig = (id(parent), _normalized_rpr(run_el))

        for child in run_el:
            tag = child.tag
            if tag == f'{W}rPr':
                continue

            if tag == f'{W}fldChar':
                flush()
                ftype = (child.get(f'{W}fldCharType') or '').lower()
                if ftype == 'begin':
                    field_instr_stack.append(True)
                elif ftype == 'separate' and field_instr_stack:
                    field_instr_stack[-1] = False
                elif ftype == 'end' and field_instr_stack:
                    field_instr_stack.pop()
                after_break = False
                continue

            if tag in _BREAK_RUN_CHILDREN:
                flush()
                after_break = True
                continue

            if tag in _PROTECTED_RUN_CHILDREN or tag != f'{W}t':
                flush()
                after_break = False
                continue

            # Child is <w:t>
            in_field_instr = bool(field_instr_stack and field_instr_stack[-1])
            if in_field_instr:
                flush()
                continue

            seg_text = child.text or ''
            # Keep TOC page numbers ("\t12") and pure numeric field results untouched.
            if (after_break or field_instr_stack) and not any(ch.isalpha() for ch in seg_text):
                flush()
                after_break = False
                continue

            after_break = False
            if current is not None and current['sig'] == sig:
                current['t_elements'].append(child)
                current['text'] += seg_text
            else:
                flush()
                current = {
                    'sig': sig,
                    't_elements': [child],
                    'text': seg_text,
                }

    flush()
    return groups


def _format_groups_text(groups: list[dict[str, Any]]) -> str:
    if len(groups) == 1:
        return groups[0]['text']
    return ''.join(f'<s{idx}>{group["text"]}</s{idx}>' for idx, group in enumerate(groups))


def _set_t_text(t_el: etree._Element, text: str) -> None:
    t_el.text = text
    if text and (text[0].isspace() or text[-1].isspace() or '\n' in text):
        t_el.set(XML_SPACE_ATTR, 'preserve')
    elif XML_SPACE_ATTR in t_el.attrib:
        del t_el.attrib[XML_SPACE_ATTR]


def extract_docx(input_docx: Path, work_dir: Path, *, chunk_chars: int = 9000) -> dict[str, Any]:
    if not input_docx.is_file():
        raise FileNotFoundError(f'Input file not found: {input_docx}')
    unpacked = work_dir / 'unpacked'
    chunks_dir = work_dir / 'chunks'
    if unpacked.exists():
        shutil.rmtree(unpacked)
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    unpacked.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(input_docx, 'r') as zf:
        safe_extract(zf, unpacked)
    for item in unpacked.rglob('*'):
        if item.is_symlink():
            item.unlink()

    merge_runs(str(unpacked))

    parts = _discover_xml_parts(unpacked)
    items: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []
    drawing_count = 0
    sdt_items = 0
    table_items = 0
    mixed_items = 0
    counter = 0

    for part_rel in parts:
        part_path = unpacked / part_rel
        tree = etree.parse(str(part_path), _PARSER)
        root = tree.getroot()
        drawing_count += len(root.findall(f'.//{W}drawing')) + len(root.findall(f'.//{W}pict'))

        for p_idx, p_el in enumerate(root.iter(f'{W}p')):
            groups = _extract_paragraph_groups(p_el)
            if not groups:
                continue
            combined_raw = ''.join(g['text'] for g in groups)
            if not any(ch.isalpha() for ch in combined_raw):
                continue

            counter += 1
            item_id = f'p{counter:04d}'
            ctx = _paragraph_context(p_el)
            style = _paragraph_style(p_el)
            formatted = _format_groups_text(groups)

            if len(groups) > 1:
                mixed_items += 1
            if ctx['in_sdt']:
                sdt_items += 1
            if ctx['in_table']:
                table_items += 1

            entry: dict[str, Any] = {
                'id': item_id,
                'text': formatted,
            }
            if style:
                entry['style'] = style
            if part_rel != 'word/document.xml':
                entry['part'] = part_rel
            items.append(entry)
            manifest_entries.append(
                {
                    'id': item_id,
                    'part': part_rel,
                    'p_index': p_idx,
                    'group_count': len(groups),
                    'style': style,
                    **ctx,
                }
            )

    # Pack into bounded chunks for LLM/subtask processing.
    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    current_chars = 2  # []
    for item in items:
        item_chars = len(json.dumps(item, ensure_ascii=False)) + 2
        if current_chunk and current_chars + item_chars > chunk_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 2
        current_chunk.append(item)
        current_chars += item_chars
    if current_chunk:
        chunks.append(current_chunk)

    chunk_files: list[str] = []
    for idx, chunk in enumerate(chunks):
        chunk_file = chunks_dir / f'chunk_{idx:03d}.json'
        chunk_file.write_text(json.dumps(chunk, ensure_ascii=False, indent=2), encoding='utf-8')
        chunk_files.append(chunk_file.as_posix())

    manifest = {
        'source_docx': input_docx.as_posix(),
        'parts': parts,
        'total_items': len(items),
        'mixed_style_items': mixed_items,
        'sdt_items': sdt_items,
        'table_items': table_items,
        'drawing_count': drawing_count,
        'chunk_count': len(chunks),
        'items': manifest_entries,
    }
    (work_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    return {
        'work_dir': work_dir.as_posix(),
        'parts': parts,
        'total_items': len(items),
        'mixed_style_items': mixed_items,
        'sdt_items': sdt_items,
        'table_items': table_items,
        'drawing_count': drawing_count,
        'chunk_count': len(chunks),
        'chunks_dir': chunks_dir.as_posix(),
    }


def _strip_json_fences(raw: str) -> str:
    match = _FENCE_RE.match(raw)
    return match.group(1) if match else raw


def _load_translations(translated_path: Path) -> dict[str, str]:
    files = sorted(translated_path.glob('*.json')) if translated_path.is_dir() else [translated_path]
    if not files:
        raise FileNotFoundError(f'No JSON translation files found at {translated_path}')

    mapping: dict[str, str] = {}
    for file_path in files:
        raw = _strip_json_fences(file_path.read_text(encoding='utf-8').strip())
        data = json.loads(raw)
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str):
                    mapping[str(key)] = value
        elif isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                key = entry.get('id') if 'id' in entry else entry.get('i')
                text = entry.get('text')
                if key is not None and isinstance(text, str):
                    mapping[str(key)] = text
        else:
            raise ValueError(f'Unsupported JSON payload in {file_path}')
    return mapping


def _parse_segment_tags(text: str, group_count: int) -> list[str] | None:
    matches = list(_SEGMENT_TAG_RE.finditer(text))
    if len(matches) != group_count:
        return None
    segments: dict[int, str] = {}
    for match in matches:
        idx = int(match.group(1))
        if idx in segments or not (0 <= idx < group_count):
            return None
        segments[idx] = match.group(2)
    if len(segments) != group_count:
        return None
    return [segments[idx] for idx in range(group_count)]


def apply_translations(work_dir: Path, translated_path: Path, output_docx: Path) -> dict[str, Any]:
    manifest_path = work_dir / 'manifest.json'
    unpacked = work_dir / 'unpacked'
    if not manifest_path.is_file() or not unpacked.is_dir():
        raise FileNotFoundError(f'Workspace extract not found in {work_dir}')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    translations = _load_translations(translated_path)

    by_part: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest['items']:
        by_part.setdefault(entry['part'], []).append(entry)

    applied = 0
    missing = 0
    tag_fallbacks = 0
    warnings: list[str] = []

    for part_rel, part_entries in by_part.items():
        part_path = unpacked / part_rel
        tree = etree.parse(str(part_path), _PARSER)
        root = tree.getroot()
        paragraphs = list(root.iter(f'{W}p'))
        part_modified = False

        for entry in part_entries:
            item_id = entry['id']
            if item_id not in translations:
                missing += 1
                if len(warnings) < 20:
                    warnings.append(f'Missing translation for {item_id}')
                continue

            p_idx = int(entry['p_index'])
            if p_idx >= len(paragraphs):
                raise RuntimeError(f'Paragraph index {p_idx} out of range in {part_rel}')
            groups = _extract_paragraph_groups(paragraphs[p_idx])
            if len(groups) != int(entry['group_count']):
                raise RuntimeError(
                    f'Group count mismatch for {item_id} in {part_rel}: {len(groups)} != {entry["group_count"]}'
                )

            new_text = translations[item_id]
            if len(groups) == 1:
                cleaned = _ANY_SEGMENT_TAG_RE.sub('', new_text)
                _set_t_text(groups[0]['t_elements'][0], cleaned)
                for extra_t in groups[0]['t_elements'][1:]:
                    _set_t_text(extra_t, '')
            else:
                parsed = _parse_segment_tags(new_text, len(groups))
                if parsed is not None:
                    for group, seg_text in zip(groups, parsed):
                        _set_t_text(group['t_elements'][0], seg_text)
                        for extra_t in group['t_elements'][1:]:
                            _set_t_text(extra_t, '')
                else:
                    tag_fallbacks += 1
                    if len(warnings) < 20:
                        warnings.append(f'Segment tag fallback for {item_id}')
                    cleaned = _ANY_SEGMENT_TAG_RE.sub('', new_text)
                    _set_t_text(groups[0]['t_elements'][0], cleaned)
                    for extra_t in groups[0]['t_elements'][1:]:
                        _set_t_text(extra_t, '')
                    for group in groups[1:]:
                        for t_el in group['t_elements']:
                            _set_t_text(t_el, '')

            applied += 1
            part_modified = True

        if part_modified:
            part_path.write_bytes(
                etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
            )

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    rezip(unpacked, output_docx)

    return {
        'output': output_docx.as_posix(),
        'applied': applied,
        'missing': missing,
        'tag_fallbacks': tag_fallbacks,
        'warnings': warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Extract and re-inject OOXML text in DOCX files.')
    sub = parser.add_subparsers(dest='command', required=True)

    p_ext = sub.add_parser('extract', help='Unpack DOCX and extract clean JSON chunks')
    p_ext.add_argument('input_docx', type=Path)
    p_ext.add_argument('work_dir', type=Path)
    p_ext.add_argument('--chunk-chars', type=int, default=9000)

    p_app = sub.add_parser('apply', help='Re-inject translated JSON chunks and repack DOCX')
    p_app.add_argument('work_dir', type=Path)
    p_app.add_argument('translated_path', type=Path)
    p_app.add_argument('output_docx', type=Path)

    args = parser.parse_args(argv)
    if args.command == 'extract':
        report = extract_docx(args.input_docx, args.work_dir, chunk_chars=max(1000, args.chunk_chars))
    else:
        report = apply_translations(args.work_dir, args.translated_path, args.output_docx)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
