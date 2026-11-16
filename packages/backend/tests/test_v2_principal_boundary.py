"""Exercise the V2 principal dependency without booting storage or the model stack.

Compile the production dependency declaration in isolation; FastAPI resolves its
real Depends graph against a controlled authentication service double. Full
session/RBAC integration remains covered by the product auth and staging suites.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request


@pytest.fixture
def principal_app():
    source = Path(__file__).parents[1] / 'open_webui/mini_hermes/v2/routes.py'
    tree = ast.parse(source.read_text())
    declaration = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_current_principal'
    )

    async def verified_user(request: Request):
        if request.headers.get('Authorization') != 'Bearer valid-test-session':
            raise HTTPException(401, 'Authentication required')
        return SimpleNamespace(id='authenticated-user')

    namespace = {'Depends': Depends, 'get_verified_user': verified_user, 'Request': Request}
    exec(compile(ast.Module(body=[declaration], type_ignores=[]), str(source), 'exec'), namespace)
    principal = namespace['get_current_principal']
    app = FastAPI()

    @app.get('/principal')
    async def identity(user_id: str = Depends(principal)):
        return {'id': user_id}

    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [{}, {'X-User-Id': 'victim'}, {'Authorization': 'Bearer invalid', 'X-User-Id': 'victim'}])
async def test_v2_requires_verified_authentication(principal_app, headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=principal_app), base_url='http://test') as client:
        response = await client.get('/principal', headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_v2_ignores_spoofed_identity_for_authenticated_users(principal_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=principal_app), base_url='http://test') as client:
        response = await client.get('/principal', headers={
            'Authorization': 'Bearer valid-test-session', 'X-User-Id': 'victim'
        })
    assert response.status_code == 200
    assert response.json() == {'id': 'authenticated-user'}


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', [
    'inspect_document_endpoint', 'read_spreadsheet_range_endpoint',
    'read_range_endpoint', 'search_document_endpoint',
])
async def test_document_reads_check_permission_before_loading_content(endpoint):
    """Run production endpoint bodies with storage/authz doubles at the boundary."""
    import __future__
    import uuid
    from unittest.mock import AsyncMock

    source = Path(__file__).parents[1] / 'open_webui/mini_hermes/v2/routes.py'
    tree = ast.parse(source.read_text())
    declaration = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == endpoint)
    declaration.decorator_list = []

    class Denied(Exception):
        pass

    def raise_http_error(error):
        raise HTTPException(403, 'Permission denied') from error

    document = SimpleNamespace(id=uuid.uuid4(), format_type='xlsx', current_revision_id=None)
    db = SimpleNamespace(get=AsyncMock(return_value=document))
    check_permission = AsyncMock(side_effect=Denied())
    namespace = {
        'Depends': Depends, 'get_v2_db': lambda: None, 'get_current_principal': lambda: None,
        'uuid': uuid, 'HTTPException': HTTPException, 'MhDocument': object(),
        'RevisionError': Denied, '_raise_revision_http_error': raise_http_error,
        'verify_current_document_permission': check_permission,
    }
    exec(compile(ast.Module(body=[declaration], type_ignores=[]), str(source), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    kwargs = {'document_id': str(document.id), 'db': db, 'principal': 'unauthorized-user'}
    if endpoint != 'inspect_document_endpoint':
        kwargs['body'] = SimpleNamespace()
    with pytest.raises(HTTPException) as error:
        await namespace[endpoint](**kwargs)
    assert error.value.status_code == 403
    check_permission.assert_awaited_once_with(db, document, 'unauthorized-user', 'read')
    # No index factory, query, or content read is reached after denial.
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_spreadsheet_rejects_a_sheet_from_another_build():
    import __future__
    import uuid
    from unittest.mock import AsyncMock

    source = Path(__file__).parents[1] / 'open_webui/mini_hermes/v2/routes.py'
    tree = ast.parse(source.read_text())
    declaration = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == 'read_spreadsheet_range_endpoint')
    declaration.decorator_list = []
    document = SimpleNamespace(id=uuid.uuid4(), format_type='xlsx', current_revision_id=uuid.uuid4())
    revision = SimpleNamespace(id=document.current_revision_id)
    build = SimpleNamespace(id=uuid.uuid4())
    foreign_sheet = SimpleNamespace(id=uuid.uuid4(), index_build_id=uuid.uuid4())
    db = SimpleNamespace(get=AsyncMock(side_effect=[document, revision, foreign_sheet]))
    manager = SimpleNamespace(get_active_index_for_revision=AsyncMock(return_value=(None, build)))
    namespace = {
        'Depends': Depends, 'get_v2_db': lambda: None, 'get_current_principal': lambda: None,
        'uuid': uuid, 'HTTPException': HTTPException,
        'MhDocument': object(), 'MhDocumentRevision': object(), 'MhWorkbookSheet': object(),
        'verify_current_document_permission': AsyncMock(),
        'get_v2_session_factory': lambda: None, 'IndexLifecycleManager': lambda factory: manager,
    }
    exec(compile(ast.Module(body=[declaration], type_ignores=[]), str(source), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    with pytest.raises(HTTPException) as error:
        await namespace['read_spreadsheet_range_endpoint'](
            document_id=str(document.id), db=db, principal='owner',
            body=SimpleNamespace(sheet_id=str(foreign_sheet.id), sheet_name=None),
        )
    assert error.value.status_code == 404
    assert 'Worksheet' in error.value.detail
