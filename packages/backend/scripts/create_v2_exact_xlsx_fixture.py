"""Generate a synthetic XLSX whose current V1 rendering is exactly 57,765 characters.

This is not a copy of the original workbook. ZIP metadata can change on regeneration.
"""
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from open_webui.mini_hermes.parsers import extract_sections

TARGET = 57_765
PATH = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'v2' / 'xlsx-f05-exact-57765.xlsx'


def workbook_bytes(workbook):
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def render(content):
    return "\n\n".join(f"## {name}\n{text}" if name else text for name, text, _ in extract_sections(content, PATH.name))


if __name__ == '__main__':
    workbook = Workbook()
    first = workbook.active
    first.title = 'Interview'
    first.append(['Question', 'Response'])
    first.append(['Synthetic question', 'Bắt đầu\n' + 'x' * 30_000])
    first.append(['Second answer', 'y'])
    first.merge_cells('A5:B5')
    first['A5'] = 'Merged synthetic note'
    second = workbook.create_sheet('Metrics')
    second.append(['Metric', 'Value'])
    second.append(['Count', 7])
    second['B3'] = '=B2*2'
    third = workbook.create_sheet('Hidden')
    third.sheet_state = 'hidden'
    third['A1'] = 'Hidden synthetic value'

    initial = workbook_bytes(workbook)
    current = len(render(initial))
    missing = TARGET - current
    if missing < 0 or len(first['B3'].value) + missing > 32_767:
        raise RuntimeError('Fixture cannot fit within Excel cell limits')
    first['B3'] = first['B3'].value + 'y' * missing
    result = workbook_bytes(workbook)
    if len(render(result)) != TARGET:
        raise RuntimeError('Fixture render length differs from target')
    PATH.write_bytes(result)
