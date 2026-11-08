import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / 'open_webui' / 'mini_hermes'


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix('')
    parts = relative.parts[:-1] if relative.name == '__init__' else relative.parts
    return '.'.join(('open_webui', 'mini_hermes', *parts))


def _application_modules() -> dict[str, Path]:
    return {
        _module_name(path): path
        for path in PACKAGE_ROOT.rglob('*.py')
        if 'skills' not in path.relative_to(PACKAGE_ROOT).parts
    }


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, path: Path, module_name: str, top_level_only: bool):
        self.path = path
        self.module_name = module_name
        self.top_level_only = top_level_only
        self.imported = set()

    def visit_Import(self, node):
        self.imported.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node):
        if node.level:
            package_parts = self.module_name.split('.')
            if self.path.name != '__init__.py':
                package_parts = package_parts[:-1]
            base_parts = package_parts[: len(package_parts) - node.level + 1]
            if node.module:
                base_parts.extend(node.module.split('.'))
            base = '.'.join(base_parts)
        else:
            base = node.module
        if base:
            self.imported.add(base)
            self.imported.update(f'{base}.{alias.name}' for alias in node.names)

    def visit_FunctionDef(self, node):
        if not self.top_level_only:
            self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        if not self.top_level_only:
            self.generic_visit(node)


def _imports(path: Path, module_name: str, *, top_level_only: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    collector = _ImportCollector(path, module_name, top_level_only)
    collector.visit(tree)
    return collector.imported


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visited = set()
    active = []
    cycles = []

    def visit(module: str) -> None:
        if module in active:
            cycles.append(active[active.index(module) :] + [module])
            return
        if module in visited:
            return
        active.append(module)
        for dependency in graph[module]:
            visit(dependency)
        active.pop()
        visited.add(module)

    for module in graph:
        visit(module)
    return cycles


def test_resource_modules_do_not_import_chat_entrypoints_or_middleware():
    forbidden = ('open_webui.main', 'open_webui.utils.middleware')
    violations = []
    for module_name, path in _application_modules().items():
        for imported in _imports(path, module_name):
            if imported in forbidden:
                violations.append(f'{path}: {imported}')
    assert not violations, '\n'.join(violations)


def test_resource_module_import_graph_is_acyclic():
    modules = _application_modules()
    graph = {module_name: _imports(path, module_name) & set(modules) for module_name, path in modules.items()}

    cycles = _find_cycles(graph)

    assert not cycles, '\n'.join(' -> '.join(cycle) for cycle in cycles)
