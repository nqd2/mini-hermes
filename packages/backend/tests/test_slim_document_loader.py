from io import BytesIO

from openpyxl import Workbook
from fastapi import HTTPException

from open_webui.retrieval.loaders.main import Loader


def _config():
    return {
        'rag.content_extraction_engine': '',
        'rag.file.max_size': 20,
        'rag.datalab_marker_api_key': '',
        'rag.external_document_loader_url': '',
        'rag.external_document_loader_api_key': '',
        'rag.tika_server_url': '',
    }


def test_slim_loader_extracts_xlsx_without_external_service(tmp_path, monkeypatch):
    from open_webui.retrieval.loaders import main

    monkeypatch.setattr(main, 'USE_SLIM', True)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sales'
    sheet['A1'] = 'Revenue'
    sheet['B1'] = 0
    path = tmp_path / 'sales.xlsx'
    book.save(path)

    docs = Loader(_config()).load('sales.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', str(path))

    assert len(docs) == 1
    assert 'Sales!A1: Revenue\t0' in docs[0].page_content


def test_slim_loader_reports_corrupt_xlsx_as_invalid_document(tmp_path, monkeypatch):
    from open_webui.retrieval.loaders import main

    monkeypatch.setattr(main, 'USE_SLIM', True)
    path = tmp_path / 'broken.xlsx'
    path.write_bytes(b'not an xlsx archive')

    try:
        Loader(_config()).load('broken.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', str(path))
    except HTTPException as exc:
        assert exc.status_code == 400
        assert 'Unable to read XLSX document' in str(exc.detail)
        assert 'BadZipFile' in str(exc.detail)
    else:
        raise AssertionError('corrupt workbook should fail validation')
