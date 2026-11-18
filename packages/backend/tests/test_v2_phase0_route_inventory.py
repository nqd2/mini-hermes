"""Checks for a static audit helper; none imports the application or opens a DB."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'phase0_route_inventory.py'
spec = importlib.util.spec_from_file_location('phase0_route_inventory', SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def test_extracts_routes_dependencies_and_dynamic_methods():
    source = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/v1")
@router.get("/items/{item_id}")
async def get_item(item_id: str, user=Depends(current_user)): pass
@router.api_route("/proxy", methods=PROXY_METHODS)
def proxy(): pass
'''
    result = inventory.scan_routes(source, 'example.py')
    assert result['router_prefixes']['router'] == '/v1'
    assert result['routes'][0]['path'] == '/items/{item_id}'
    assert result['routes'][0]['methods'] == ['GET']
    assert result['routes'][0]['handler'] == 'get_item'
    assert 'Depends(current_user)' in result['routes'][0]['dependencies']
    assert result['routes'][1]['methods'] is None
    assert result['routes'][1]['methods_expression'] == 'PROXY_METHODS'


def test_records_multiple_decorators_without_claiming_dynamic_paths():
    source = '''
@app.post("/one")
@app.post(DYNAMIC_PATH)
async def endpoint(): pass
'''
    routes = inventory.scan_routes(source, 'main.py')['routes']
    assert len(routes) == 2
    assert routes[0]['path'] == '/one'
    assert routes[1]['path'] is None
    assert routes[1]['path_expression'] == 'DYNAMIC_PATH'


def test_mounts_retain_import_aliases_and_conditional_status():
    source = '''
from weai.routers import auths
from weai.documents.routes import router as document_router
app.include_router(auths.router, prefix='/api/v1/auths')
if ENABLE_DOCUMENTS:
    app.include_router(document_router, prefix='/api/v1/agent-runtime')
'''
    mounts = inventory.scan_mounts(source)
    assert mounts[0]['module'] == 'weai.routers.auths'
    assert mounts[0]['prefix'] == '/api/v1/auths'
    assert mounts[0]['conditional'] is False
    assert mounts[1]['module'] == 'weai.documents.routes'
    assert mounts[1]['conditional'] is True


def test_nested_router_registration_and_nonliteral_prefix_are_flagged():
    result = inventory.scan_routes('''
router = APIRouter(prefix=PREFIX)
router.include_router(child.router)
@router.get('/test')
def endpoint(): pass
''', 'example.py')
    assert result['router_prefixes']['router'] is None
    assert len(result['nested_routers']) == 1


def test_exact_static_pairing_excludes_dynamic_paths_and_unknown_methods():
    front = {'entries': [
        {'file': 'src/auth.ts', 'line': 1, 'caller': 'signin', 'method': 'POST',
         'url_expression': '`${WEAI_API_BASE_URL}/auths/signin`'},
        {'file': 'src/auth.ts', 'line': 2, 'caller': 'missing', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/does-not-exist`'},
        {'file': 'src/chat.ts', 'line': 3, 'caller': 'lookup', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/chats/${id}`'},
        {'file': 'src/chat.ts', 'line': 4, 'caller': 'unknown', 'method': 'UNKNOWN',
         'url_expression': '`${WEAI_API_BASE_URL}/auths/signin`'},
    ]}
    back = {'routes': [{'mounted_path': '/api/v1/auths/signin', 'methods': ['POST'], 'handler': 'signin'}]}
    result = inventory.pair_static_routes(front, back)
    assert len(result['matched']) == 1
    assert result['matched'][0]['producers'][0]['handler'] == 'signin'
    assert len(result['no_exact_match']) == 1
    assert len(result['unresolved']) == 2


def test_parameterized_paths_are_candidates_but_wildcards_stay_unresolved():
    front = {'entries': [
        {'file': 'src/a.ts', 'line': 1, 'caller': 'load', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/chats/${id}`'},
        {'file': 'src/a.ts', 'line': 2, 'caller': 'load_encoded', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/chats/${encodeURIComponent(id)}?${params}`'},
        {'file': 'src/a.ts', 'line': 3, 'caller': 'path', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/files/${path}`'},
        {'file': 'src/a.ts', 'line': 4, 'caller': 'dynamic', 'method': 'GET',
         'url_expression': '`${WEAI_API_BASE_URL}/chats/${getPath()}`'},
    ]}
    back = {'routes': [
        {'mounted_path': '/api/v1/chats/{chat_id}', 'methods': ['GET'], 'handler': 'get_chat'},
        {'mounted_path': '/api/v1/chats/new', 'methods': ['GET'], 'handler': 'static'},
        {'mounted_path': '/api/v1/files/{file_path:path}', 'methods': ['GET'], 'handler': 'wildcard'},
    ]}
    result = inventory.pair_static_routes(front, back)
    assert len(result['matched_template']) == 2
    assert all(item['producers'][0]['handler'] == 'get_chat' for item in result['matched_template'])
    assert len(result['unresolved']) == 2


def test_known_product_base_paths_resolve_but_ollama_and_unknown_bases_do_not():
    front = {'entries': [
        {'method': 'GET', 'url_expression': '`${AUDIO_API_BASE_URL}/config`'},
        {'method': 'GET', 'url_expression': '`${RETRIEVAL_API_BASE_URL}/config`'},
        {'method': 'GET', 'url_expression': '`${OLLAMA_API_BASE_URL}/config`'},
        {'method': 'GET', 'url_expression': '`${url}/config`'},
    ]}
    back = {'routes': [
        {'mounted_path': '/api/v1/audio/config', 'methods': ['GET']},
        {'mounted_path': '/api/v1/retrieval/config', 'methods': ['GET']},
        {'mounted_path': '/ollama/config', 'methods': ['GET']},
    ]}
    result = inventory.pair_static_routes(front, back)
    assert len(result['matched']) == 2
    assert len(result['unresolved']) == 2
