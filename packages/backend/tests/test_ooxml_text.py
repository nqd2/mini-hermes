import json
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree

from weai.agent_runtime.skills.docx.scripts.ooxml_text import W, W_NS, apply_translations, extract_docx


def _build_complex_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading('CHAPTER 1. INTRODUCTION', level=1)

    # Mixed bold/italic paragraph (like RO1 and Keywords)
    p_ro = doc.add_paragraph()
    r1 = p_ro.add_run('RO1.')
    r1.bold = True
    p_ro.add_run(' To analyze Vietnam construction value chain.')

    p_kw = doc.add_paragraph()
    rk1 = p_kw.add_run('Keywords: ')
    rk1.bold = True
    rk2 = p_kw.add_run('supply chain finance; ')
    rk2.italic = True
    rk3 = p_kw.add_run('Techcombank')
    rk3.italic = True

    # Table
    tbl = doc.add_table(rows=2, cols=2)
    tbl.cell(0, 0).text = 'Sector'
    tbl.cell(0, 1).text = 'Focus'
    tbl.cell(1, 0).text = 'Construction'
    tbl.cell(1, 1).text = 'Working capital'

    # Footer
    doc.sections[0].footer.paragraphs[0].text = 'Confidential Thesis Footer'
    doc.save(str(path))

    # Inject SDT (TOC), fldChar/instrText, w:drawing, and footnotes.xml at OOXML level.
    unpacked = path.parent / '_seed_unpacked'
    with zipfile.ZipFile(path, 'r') as zf:
        zf.extractall(unpacked)

    doc_xml = unpacked / 'word' / 'document.xml'
    tree = etree.parse(str(doc_xml))
    body = tree.getroot().find(f'{W}body')

    # Add a TOC w:sdt containing a paragraph with tab + page number
    sdt = etree.SubElement(body, f'{W}sdt')
    sdt_content = etree.SubElement(sdt, f'{W}sdtContent')
    toc_p = etree.SubElement(sdt_content, f'{W}p')
    r_toc = etree.SubElement(toc_p, f'{W}r')
    t_toc = etree.SubElement(r_toc, f'{W}t')
    t_toc.text = 'Table 1.1: Market Overview'
    r_pg = etree.SubElement(toc_p, f'{W}r')
    etree.SubElement(r_pg, f'{W}tab')
    t_pg = etree.SubElement(r_pg, f'{W}t')
    t_pg.text = '12'

    # Add a paragraph with a Word field (begin -> instrText -> separate -> display -> end)
    fld_p = etree.SubElement(body, f'{W}p')
    r_b = etree.SubElement(fld_p, f'{W}r')
    fc_b = etree.SubElement(r_b, f'{W}fldChar')
    fc_b.set(f'{W}fldCharType', 'begin')
    r_i = etree.SubElement(fld_p, f'{W}r')
    instr = etree.SubElement(r_i, f'{W}instrText')
    instr.text = ' REF _Ref123 \\h '
    r_s = etree.SubElement(fld_p, f'{W}r')
    fc_s = etree.SubElement(r_s, f'{W}fldChar')
    fc_s.set(f'{W}fldCharType', 'separate')
    r_d = etree.SubElement(fld_p, f'{W}r')
    t_d = etree.SubElement(r_d, f'{W}t')
    t_d.text = 'Figure 1.1'
    r_e = etree.SubElement(fld_p, f'{W}r')
    fc_e = etree.SubElement(r_e, f'{W}fldChar')
    fc_e.set(f'{W}fldCharType', 'end')

    # Add a paragraph containing a drawing marker alongside text
    draw_p = etree.SubElement(body, f'{W}p')
    r_draw = etree.SubElement(draw_p, f'{W}r')
    etree.SubElement(r_draw, f'{W}drawing')
    r_cap = etree.SubElement(draw_p, f'{W}r')
    t_cap = etree.SubElement(r_cap, f'{W}t')
    t_cap.text = 'Diagram caption'

    # Move sectPr to end if present
    sect = body.find(f'{W}sectPr')
    if sect is not None:
        body.remove(sect)
        body.append(sect)

    doc_xml.write_bytes(etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True))

    # Add word/footnotes.xml
    fn_root = etree.Element(f'{W}footnotes', nsmap={'w': W_NS})
    fn = etree.SubElement(fn_root, f'{W}footnote', {f'{W}id': '1'})
    fn_p = etree.SubElement(fn, f'{W}p')
    fn_r = etree.SubElement(fn_p, f'{W}r')
    fn_t = etree.SubElement(fn_r, f'{W}t')
    fn_t.text = 'First methodological footnote'
    (unpacked / 'word' / 'footnotes.xml').write_bytes(
        etree.tostring(fn_root, xml_declaration=True, encoding='UTF-8', standalone=True)
    )

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(unpacked.rglob('*')):
            if f.is_file():
                zf.write(f, f.relative_to(unpacked))


def test_ooxml_extract_and_apply_preserves_formatting_sdt_fields_drawings_and_parts(tmp_path):
    src_docx = tmp_path / 'thesis.docx'
    _build_complex_docx(src_docx)
    work_dir = tmp_path / 'ooxml_work'

    summary = extract_docx(src_docx, work_dir, chunk_chars=1200)
    assert summary['total_items'] >= 10
    assert summary['mixed_style_items'] >= 2  # RO1 + Keywords
    assert summary['sdt_items'] == 1
    assert summary['table_items'] == 4
    assert summary['drawing_count'] == 1
    assert 'word/footer1.xml' in summary['parts']
    assert 'word/footnotes.xml' in summary['parts']

    chunk_files = sorted((work_dir / 'chunks').glob('chunk_*.json'))
    assert chunk_files

    all_items = []
    for cf in chunk_files:
        all_items.extend(json.loads(cf.read_text(encoding='utf-8')))

    all_texts = '\n'.join(item['text'] for item in all_items)
    # Field instrText and TOC page number "\t12" must NOT be extracted.
    assert 'REF _Ref123' not in all_texts
    assert '\t12' not in all_texts
    assert '<s0>RO1.</s0><s1> To analyze Vietnam construction value chain.</s1>' in all_texts
    assert '<s0>Keywords: </s0><s1>supply chain finance; Techcombank</s1>' in all_texts
    assert 'Table 1.1: Market Overview' in all_texts
    assert 'Confidential Thesis Footer' in all_texts
    assert 'First methodological footnote' in all_texts

    translated_dir = tmp_path / 'translated'
    translated_dir.mkdir()
    for idx, cf in enumerate(chunk_files):
        chunk = json.loads(cf.read_text(encoding='utf-8'))
        out_chunk = []
        for entry in chunk:
            txt = entry['text']
            if 'RO1.' in txt:
                new_txt = '<s0>MT1.</s0><s1> Phân tích chuỗi giá trị xây dựng Việt Nam.</s1>'
            elif 'Keywords:' in txt:
                # Deliberately drop <sN> tags on one mixed item to verify safe fallback!
                new_txt = 'Từ khóa: tài trợ chuỗi cung ứng; Techcombank'
            else:
                new_txt = f'[VI] {txt}'
            out_chunk.append({'id': entry['id'], 'text': new_txt})
        payload = json.dumps(out_chunk, ensure_ascii=False, indent=2)
        if idx == 0:
            # Wrap first chunk in markdown fences to verify stripping.
            payload = f'```json\n{payload}\n```'
        (translated_dir / cf.name).write_text(payload, encoding='utf-8')

    out_docx = tmp_path / 'out' / 'thesis_vi.docx'
    apply_report = apply_translations(work_dir, translated_dir, out_docx)
    assert apply_report['missing'] == 0
    assert apply_report['tag_fallbacks'] == 1
    assert apply_report['applied'] == len(all_items)
    assert out_docx.is_file()

    # Open with python-docx and inspect XML parts directly
    rebuilt = Document(str(out_docx))
    assert rebuilt.paragraphs[0].style.name == 'Heading 1'
    assert rebuilt.paragraphs[0].text == '[VI] CHAPTER 1. INTRODUCTION'

    # RO1 retains bold on run 0 and non-bold on run 1
    ro_para = rebuilt.paragraphs[1]
    assert ro_para.runs[0].bold is True
    assert ro_para.runs[0].text == 'MT1.'
    assert not ro_para.runs[1].bold
    assert ro_para.runs[1].text == ' Phân tích chuỗi giá trị xây dựng Việt Nam.'

    # Fallback mixed item retains readable text without leaking tags
    kw_para = rebuilt.paragraphs[2]
    assert kw_para.text == 'Từ khóa: tài trợ chuỗi cung ứng; Techcombank'
    assert '<s' not in kw_para.text

    # Tables & Footer
    assert rebuilt.tables[0].cell(1, 1).text == '[VI] Working capital'
    assert rebuilt.sections[0].footer.paragraphs[0].text == '[VI] Confidential Thesis Footer'

    # Check raw XML for untouched instrText, drawing, TOC page number, and footnotes
    with zipfile.ZipFile(out_docx, 'r') as zf:
        doc_xml_str = zf.read('word/document.xml').decode('utf-8')
        fn_xml_str = zf.read('word/footnotes.xml').decode('utf-8')
    assert 'REF _Ref123' in doc_xml_str
    assert '<w:drawing' in doc_xml_str
    assert '>12<' in doc_xml_str
    assert '[VI] Table 1.1: Market Overview' in doc_xml_str
    assert '[VI] Figure 1.1' in doc_xml_str
    assert '[VI] First methodological footnote' in fn_xml_str
