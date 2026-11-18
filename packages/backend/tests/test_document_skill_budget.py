import pytest

from weai.agent_runtime.tools import read_document_skill
from weai.utils.chat_runtime.resource_execution import prepare_resource_tool_result


@pytest.mark.asyncio
async def test_default_skill_page_fits_resource_observation_budget_and_can_be_read_to_end():
    metadata = {'resource_aware_files': True, 'resource_file_context_char_budget': 5952}
    page = await read_document_skill('xlsx', __user__={'id': 'reader'})
    content, supplied = prepare_resource_tool_result(metadata, 'read_document_skill', page)
    assert supplied is True
    assert len(content) <= 5952
    assert 0 < page['next_offset'] < page['total_characters']
    text = page['text']
    while page['next_offset'] is not None:
        offset = page['next_offset']
        page = await read_document_skill('xlsx', offset=offset, __user__={'id': 'reader'})
        assert page['offset'] == offset
        text += page['text']
    assert len(text) == page['total_characters']
