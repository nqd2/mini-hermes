"""Discover FastAPI route candidates without importing the application.

This is a static review aid, not an authoritative route table or security audit.
"""

from __future__ import annotations

import ast
import argparse
import json
import re
from pathlib import Path


HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace', 'websocket'}


def _literal_string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call, name):
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _methods(decorator):
    method = decorator.func.attr
    if method in HTTP_METHODS:
        return [method.upper()], None
    value = _keyword(decorator, 'methods')
    if isinstance(value, (ast.List, ast.Tuple)):
        result = [_literal_string(item) for item in value.elts]
        if all(item is not None for item in result):
            return [item.upper() for item in result], None
    return None, ast.unparse(value) if value is not None else None


def scan_routes(source: str, filename: str) -> dict:
    tree = ast.parse(source, filename=filename)
    prefixes = {}
    nested = []
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'APIRouter':
                prefix = _keyword(value, 'prefix')
                for target in targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = '' if prefix is None else _literal_string(prefix)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'include_router':
            if isinstance(node.func.value, ast.Name) and node.func.value.id != 'app':
                nested.append({'line': node.lineno, 'expression': ast.unparse(node)})
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dependencies = sorted({
            ast.unparse(item)
            for value in [*node.args.defaults, *[value for value in node.args.kw_defaults if value is not None]]
            for item in ast.walk(value)
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == 'Depends'
        })
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in HTTP_METHODS | {'api_route'}:
                continue
            if not isinstance(decorator.func.value, ast.Name):
                continue
            if not decorator.args:
                continue
            route_path = _literal_string(decorator.args[0])
            methods, methods_expression = _methods(decorator)
            route_dependencies = _keyword(decorator, 'dependencies')
            route = {
                'source': filename, 'line': decorator.lineno, 'handler': node.name,
                'router': decorator.func.value.id, 'path': route_path,
                'path_expression': ast.unparse(decorator.args[0]) if route_path is None else None,
                'methods': methods, 'methods_expression': methods_expression,
                'dependencies': dependencies,
                'route_dependencies_expression': ast.unparse(route_dependencies) if route_dependencies else None,
            }
            routes.append(route)
    return {'router_prefixes': prefixes, 'nested_routers': nested, 'routes': routes}


def scan_mounts(source: str) -> list[dict]:
    tree = ast.parse(source)
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('weai.'):
            for alias in node.names:
                if node.module == 'weai.routers':
                    module = f'{node.module}.{alias.name}'
                else:
                    module = node.module
                imports[alias.asname or alias.name] = module
    mounts = []

    def visit(node, conditional=False):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'include_router':
            if isinstance(node.func.value, ast.Name) and node.func.value.id == 'app' and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name):
                    alias = argument.value.id
                elif isinstance(argument, ast.Name):
                    alias = argument.id
                else:
                    alias = None
                prefix = _keyword(node, 'prefix')
                mounts.append({
                    'line': node.lineno, 'argument': ast.unparse(argument),
                    'module': imports.get(alias),
                    'prefix': '' if prefix is None else _literal_string(prefix),
                    'prefix_expression': ast.unparse(prefix) if prefix is not None and _literal_string(prefix) is None else None,
                    'conditional': conditional,
                })
        for child in ast.iter_child_nodes(node):
            visit(child, conditional or isinstance(node, (ast.If, ast.Try, ast.For, ast.While)))

    visit(tree)
    return mounts


def inventory(root: Path) -> dict:
    package = root / 'weai'
    mounts = scan_mounts((package / 'main.py').read_text(encoding='utf-8'))
    by_module = {}
    for mount in mounts:
        if mount['module']:
            by_module.setdefault(mount['module'], []).append(mount)
    result = {'source_root': 'packages/backend/weai', 'mounts': mounts, 'routes': [], 'warnings': []}
    for file in sorted(package.rglob('*.py')):
        if '__pycache__' in file.parts:
            continue
        relative = file.relative_to(root).as_posix()
        module_path = file.relative_to(root).with_suffix('')
        parts = list(module_path.parts)
        if parts[-1] == '__init__':
            parts.pop()
        module = '.'.join(parts)
        try:
            scanned = scan_routes(file.read_text(encoding='utf-8'), relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            result['warnings'].append({'source': relative, 'message': str(exc)})
            continue
        for nested in scanned['nested_routers']:
            result['warnings'].append({'source': relative, 'message': 'Nested router requires manual resolution', **nested})
        for route in scanned['routes']:
            candidates = by_module.get(module, []) if route['router'] != 'app' else (
                [{'prefix': '', 'conditional': False, 'line': None}] if module == 'weai.main' else []
            )
            prefix = scanned['router_prefixes'].get(route['router'], '' if route['router'] == 'app' else None)
            if not candidates:
                candidates = [{'prefix': None, 'conditional': None, 'line': None}]
            for mount in candidates:
                known = mount['prefix'] is not None and prefix is not None and route['path'] is not None
                result['routes'].append({
                    **route, 'module': module,
                    'mounted_path': mount['prefix'] + prefix + route['path'] if known else None,
                    'mount_line': mount['line'], 'conditional_mount': mount['conditional'],
                })
    return result


def pair_static_routes(frontend: dict, backend: dict) -> dict:
    """Join exact paths and separately suggest simple parameterized route candidates."""
    by_route = {}
    by_template = {}
    for route in backend['routes']:
        if route['mounted_path'] is not None:
            for method in route['methods'] or []:
                by_route.setdefault((method, route['mounted_path']), []).append(route)
                segments = route['mounted_path'].split('/')
                template = []
                has_parameter = False
                for segment in segments:
                    if re.fullmatch(r'\{[A-Za-z_][A-Za-z0-9_]*(?::(?:str|int|float|uuid))?\}', segment):
                        template.append('{parameter}')
                        has_parameter = True
                    elif '{' in segment or '}' in segment:
                        break  # Complex or path-converter semantics require manual inspection.
                    else:
                        template.append(segment)
                else:
                    if has_parameter:
                        by_template.setdefault((method, '/'.join(template)), []).append(route)

    result = {'matched': [], 'matched_template': [], 'no_exact_match': [], 'unresolved': []}
    bases = {
        'WEAI_BASE_URL': '',
        'WEAI_API_BASE_URL': '/api/v1',
        'OPENAI_API_BASE_URL': '/openai',
        'AUDIO_API_BASE_URL': '/api/v1/audio',
        'IMAGES_API_BASE_URL': '/api/v1/images',
        'RETRIEVAL_API_BASE_URL': '/api/v1/retrieval',
    }
    pattern = re.compile(r'^`\$\{([A-Z_]+)\}(/[^`]*)`$')
    parameter = re.compile(r'^\$\{(?:[A-Za-z_$][\w$]*|encodeURIComponent\(\s*[A-Za-z_$][\w$]*\s*\))\}$')
    for call in frontend['entries']:
        match = pattern.fullmatch(call['url_expression'])
        if not match or match.group(1) not in bases or call['method'] == 'UNKNOWN':
            result['unresolved'].append(call)
            continue
        segments = (bases[match.group(1)] + match.group(2).split('?', 1)[0]).split('/')
        path_segments = []
        dynamic = False
        for segment in segments:
            if parameter.fullmatch(segment):
                dynamic = True
                path_segments.append('{parameter}')
            elif re.fullmatch(r'[A-Za-z0-9_.%~-]*', segment):
                path_segments.append(segment)
            else:
                break
        else:
            path = '/'.join(path_segments)
            producers = (by_template if dynamic else by_route).get((call['method'], path), [])
            if dynamic and not producers:
                result['unresolved'].append(call)
                continue
            record = {'consumer': call, 'path': path, 'producers': producers}
            result['matched_template' if dynamic else 'matched' if producers else 'no_exact_match'].append(record)
            continue
        result['unresolved'].append(call)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', nargs=2, metavar=('FRONTEND_JSON', 'BACKEND_JSON'))
    args = parser.parse_args()
    if args.pair:
        frontend, backend = (json.loads(Path(path).read_text(encoding='utf-8-sig')) for path in args.pair)
        output = pair_static_routes(frontend, backend)
    else:
        output = inventory(Path(__file__).resolve().parents[1])
    print(json.dumps(output, ensure_ascii=False, indent=2))
