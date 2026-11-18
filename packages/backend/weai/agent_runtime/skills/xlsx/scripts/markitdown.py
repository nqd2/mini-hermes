"""Render a bounded, read-only Markdown preview of a chat-scoped workbook."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


MAX_ROWS_PER_SHEET = 40
MAX_COLUMNS_PER_SHEET = 24
MAX_CELL_CHARACTERS = 240
MAX_OUTPUT_CHARACTERS = 5_000
WORKBOOK_SUFFIXES = {'.xlsx', '.xlsm', '.xltx', '.xltm'}


def _workbook_path(document_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', document_id):
        raise ValueError('Expected a document ID, not a filesystem path.')

    root = Path('inputs').resolve()
    directory = (root / document_id).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Document ID resolves outside the input directory.')

    def find_workbooks(search_root: Path) -> list[Path]:
        return sorted(
            path for path in search_root.rglob('*')
            if path.is_file() and path.suffix.lower() in WORKBOOK_SUFFIXES
            and not path.is_symlink() and path.resolve().is_relative_to(root)
        )

    scoped_matches = find_workbooks(directory) if directory.is_dir() else []
    if len(scoped_matches) == 1:
        return scoped_matches[0]
    if len(scoped_matches) > 1:
        raise ValueError(
            f'Expected one Excel workbook for document {document_id}; found {len(scoped_matches)}.'
        )

    all_matches = find_workbooks(root) if root.is_dir() else []
    if len(all_matches) == 1:
        return all_matches[0]
    if not all_matches:
        raise ValueError('No Excel workbook found in the chat-scoped inputs.')
    raise ValueError(
        f'No workbook directory matches document {document_id}; '
        f'{len(all_matches)} workbooks are available. Select one workbook for this task.'
    )


def _cell_text(value: object) -> str:
    if value is None:
        return ''
    text = str(value).replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\n', '<br>').replace('|', '\\|').strip()
    if len(text) > MAX_CELL_CHARACTERS:
        text = text[:MAX_CELL_CHARACTERS] + '… [cell truncated]'
    return text


def _normalized(value: object) -> str:
    return ' '.join(_cell_text(value).replace('<br>', ' ').split())


def _sampled_row_numbers(total_rows: int) -> list[int]:
    if total_rows <= MAX_ROWS_PER_SHEET:
        return list(range(1, total_rows + 1))

    edge_count = MAX_ROWS_PER_SHEET // 4
    selected = set(range(1, edge_count + 1))
    selected.update(range(total_rows - edge_count + 1, total_rows + 1))
    middle_count = MAX_ROWS_PER_SHEET - len(selected)
    for slot in range(1, middle_count + 1):
        row_number = round(slot * (total_rows + 1) / (middle_count + 1))
        if 1 <= row_number <= total_rows:
            selected.add(row_number)

    while len(selected) < MAX_ROWS_PER_SHEET:
        ordered = sorted(selected)
        gaps = [(right - left, left, right) for left, right in zip(ordered, ordered[1:])]
        if not gaps:
            break
        _, left, right = max(gaps)
        selected.add((left + right) // 2)
    return sorted(selected)[:MAX_ROWS_PER_SHEET]


def _program_columns(row: tuple[object, ...]) -> dict[str, int] | None:
    normalized = [_normalized(value).casefold() for value in row]
    code = next((i for i, value in enumerate(normalized)
                 if 'program code' in value or '专业代码' in value), None)
    if code is None:
        return None

    def find(*terms: str) -> int | None:
        return next((i for i, value in enumerate(normalized)
                     if any(term in value for term in terms)), None)

    return {
        'code': code,
        'name': find('programs', 'program name', '专业名称'),
        'language': find('teaching language', '授课语言'),
        'duration': find('duration', '学制'),
        'degree type': find('degree type', '学位类型'),
        'enrollment': find('计划招生', 'enrollment'),
        'acceptance letter': find('acceptance letter', '接收函'),
        'scholarship visitors': find('csc visiting', '中国政府奖学金'),
        'exchange students': find('exchanging students', '校际交流'),
        'other visitors': find('other types of non-degree visiting', 'other types', '其他类型非学历进修'),
    }


def _program_code(value: object) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value >= 1000 and float(value).is_integer()
    return bool(re.fullmatch(r'\d{4,}', str(value or '').strip()))


def _counter_text(label: str, values: Counter[str]) -> str:
    if not values:
        return ''
    parts = [f'{value or "(blank)"}: {count}' for value, count in values.most_common(6)]
    return f'- {label}: ' + '; '.join(parts)


def render_workbook(path: Path) -> str:
    workbook = load_workbook(path, read_only=True, data_only=False)
    lines = [f'# Workbook: {path.name}', '', '## Sheets']
    lines.extend(
        f'- {sheet.title}: {sheet.max_row or 0} rows × {sheet.max_column or 0} columns'
        for sheet in workbook.worksheets
    )

    truncated_sheets: list[str] = []
    for sheet in workbook.worksheets:
        lines.extend(['', f'## {sheet.title}'])
        total_rows = sheet.max_row or 0
        total_columns = sheet.max_column or 0
        columns = min(total_columns, MAX_COLUMNS_PER_SHEET)
        if columns == 0 or total_rows == 0:
            lines.append('(empty sheet)')
            continue

        sampled_rows = _sampled_row_numbers(total_rows)
        sampled_row_set = set(sampled_rows)
        sampled_values: dict[int, tuple[object, ...]] = {}
        program_columns: dict[str, int | None] | None = None
        programs: list[tuple[int, tuple[object, ...]]] = []
        department_names: list[str] = []
        nonempty_rows = formula_count = error_count = 0

        for row_number, row in enumerate(
            sheet.iter_rows(max_row=total_rows, max_col=columns, values_only=True), 1
        ):
            if any(value is not None for value in row):
                nonempty_rows += 1
            for value in row:
                if isinstance(value, str):
                    formula_count += value.startswith('=')
                    error_count += value in {'#REF!', '#VALUE!', '#DIV/0!', '#NAME?', '#N/A', '#NUM!', '#NULL!'}

            if row_number in sampled_row_set:
                sampled_values[row_number] = row

            if program_columns is None:
                candidate = _program_columns(row)
                if candidate is not None:
                    program_columns = candidate
            elif _program_code(row[program_columns['code']]):
                programs.append((row_number, row))

            first_cell = _normalized(row[0]) if row else ''
            if ('学院' in first_cell and '名称' in first_cell) or ('school' in first_cell.casefold() and 'name' in first_cell.casefold()):
                if len(row) > 1 and _normalized(row[1]):
                    department_names.append(_normalized(row[1]))

        lines.append(f'- Non-empty rows: {nonempty_rows}; formula cells: {formula_count}; visible error cells: {error_count}')
        if department_names:
            lines.append(f'- Department/college sections: {len(department_names)}; examples: ' + '; '.join(department_names[:5]))

        if program_columns is not None:
            lines.append(f'- Program-code rows across the whole sheet: {len(programs)}')
            for label in ('language', 'duration', 'degree type', 'enrollment', 'acceptance letter',
                          'scholarship visitors', 'exchange students', 'other visitors'):
                column = program_columns[label]
                if column is not None:
                    counts = Counter(_normalized(row[column]) for _, row in programs)
                    summary = _counter_text(label.title(), counts)
                    if summary:
                        lines.append(summary)
            name_column = program_columns['name']
            if name_column is not None and programs:
                examples = programs[:3] + programs[-3:]
                unique_examples = list(dict.fromkeys(examples))
                lines.append('- Program examples (first and last entries): ' + '; '.join(
                    f'{_normalized(row[name_column])[:100]} [{_normalized(row[program_columns["code"]])}]'
                    for _, row in unique_examples
                ))

        headers = [f'Column {get_column_letter(index)}' for index in range(1, columns + 1)]
        lines.append('Preview source rows: ' + ', '.join(map(str, sampled_rows)))
        lines.extend(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'])
        for row_number in sampled_rows:
            row = sampled_values.get(row_number, ())
            lines.append('| ' + ' | '.join(_cell_text(value) for value in row) + ' |')
        if total_rows > MAX_ROWS_PER_SHEET or total_columns > columns:
            truncated_sheets.append(sheet.title)

    workbook.close()
    output = '\n'.join(lines)
    if truncated_sheets:
        output += '\n\nPreview limits reached for: ' + ', '.join(truncated_sheets)
    if len(output) > MAX_OUTPUT_CHARACTERS:
        output = output[:MAX_OUTPUT_CHARACTERS].rsplit('\n', 1)[0]
        output += '\n\n[Workbook preview truncated to the output limit.]'
    return output


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: markitdown.py <document-id>', file=sys.stderr)
        return 2
    try:
        path = _workbook_path(sys.argv[1])
        print(render_workbook(path))
    except Exception as error:
        print(f'Could not preview workbook: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
