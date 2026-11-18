import inspect

import pytest


@pytest.mark.asyncio
async def test_automation_tools_keep_registry_contract_after_extraction():
    from weai.tools import builtin
    from weai.tools import automation_resources

    for name in ('create_automation', 'update_automation', 'list_automations', 'toggle_automation', 'delete_automation'):
        exported = getattr(builtin, name)
        assert exported is getattr(automation_resources, name)
        assert '__request__' in inspect.signature(exported).parameters
        assert '__user__' in inspect.signature(exported).parameters
        required = {
            param.name: 'unused'
            for param in inspect.signature(exported).parameters.values()
            if param.default is inspect.Parameter.empty and not param.name.startswith('__')
        }
        assert await exported(**required, __request__=None) == '{"error":"Request context not available"}'


@pytest.mark.asyncio
async def test_calendar_tools_keep_registry_contract_after_extraction():
    from weai.tools import builtin
    from weai.tools import calendar_resources

    for name in ('search_calendar_events', 'create_calendar_event', 'update_calendar_event', 'delete_calendar_event'):
        exported = getattr(builtin, name)
        assert exported is getattr(calendar_resources, name)
        assert '__request__' in inspect.signature(exported).parameters
        assert '__user__' in inspect.signature(exported).parameters
