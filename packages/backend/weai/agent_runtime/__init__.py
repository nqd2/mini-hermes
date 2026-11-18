"""Durable agent execution and isolated sandbox services."""

from importlib import import_module

# Importing the sandbox entry point must not initialize the application database.
_EXPORT_MODULES = {
    'AgentArtifact': '.models',
    'AgentArtifactStore': '.artifacts',
    'AgentCheckpoint': '.models',
    'AgentEvent': '.models',
    'AgentFileContent': '.models',
    'AgentFileContentStore': '.files',
    'AgentRepository': '.repository',
    'AgentRuntime': '.runtime',
    'AgentSession': '.models',
    'AgentToolInvocation': '.models',
    'AgentTurn': '.models',
    'Base': 'weai.internal.db',
    'BoundedToolOutput': '.outputs',
    'ContextManager': '.context',
    'ConversationEngineBinding': '.contracts',
    'FileContentReference': '.files',
    'FileMatch': '.files',
    'FileMetadata': '.files',
    'LargeFileReader': '.files',
    'PreparedContext': '.context',
    'ProviderGateway': '.gateway',
    'RequestBudgetExceeded': '.request_budget',
    'RequestBudgetLimits': '.request_budget',
    'RequestBudgetManager': '.request_budget',
    'RunBinding': '.contracts',
    'ToolOutputManager': '.outputs',
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORT_MODULES[name], __name__), name)
    globals()[name] = value
    return value
