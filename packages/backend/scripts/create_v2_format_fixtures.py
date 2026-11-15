"""Create synthetic Phase 0 format fixtures; output contains no user data.

Files are generated once and checked in with their SHA-256 checksums. Office ZIP
metadata and PDF internals may vary by library version, so regenerating is an
explicit fixture revision, not a byte-for-byte reproducible build.
"""
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'v2'
OUT.mkdir(parents=True, exist_ok=True)


def create_docx():
    doc = Document()
    doc.add_paragraph('Synthetic interview: opening paragraph.')
    p = doc.add_paragraph('Link: ')
    relation = p.part.relate_to('https://example.invalid/synthetic', RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), relation)
    run = OxmlElement('w:r')
    text = OxmlElement('w:t')
    text.text = 'external reference'
    run.append(text)
    hyperlink.append(run)
    p._p.append(hyperlink)
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = 'Question'
    table.cell(0, 1).text = 'Multiline\nanswer'
    doc.sections[0].header.paragraphs[0].text = 'Synthetic header'
    doc.sections[0].footer.paragraphs[0].text = 'Synthetic footer'
    doc.save(OUT / 'docx-f01-structured.docx')


def create_pptx():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = 'Synthetic slide one'
    slide.placeholders[1].text = 'Placeholder content'
    group = slide.shapes.add_group_shape()
    text_box = group.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    text_box.text_frame.text = 'Grouped content'
    second = prs.slides.add_slide(prs.slide_layouts[6])
    second.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = 'Second slide text'
    prs.save(OUT / 'pptx-f01-structured.pptx')


def create_pdf():
    path = OUT / 'pdf-f01-text-and-image.pdf'
    output = canvas.Canvas(str(path), invariant=1)
    output.drawString(72, 720, 'Synthetic selectable text layer')
    output.showPage()
    pixels = Image.new('RGB', (16, 16), color=(20, 80, 160))
    buffer = BytesIO()
    pixels.save(buffer, format='PNG')
    buffer.seek(0)
    output.drawImage(ImageReader(buffer), 72, 600, width=128, height=128)
    output.showPage()
    output.save()


if __name__ == '__main__':
    create_docx()
    create_pptx()
    create_pdf()
