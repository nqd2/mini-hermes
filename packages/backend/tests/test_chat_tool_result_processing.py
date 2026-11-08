import pytest
from fastapi.responses import HTMLResponse

from open_webui.utils.chat_runtime.tool_results import process_tool_result
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.mcp.apps import MCPAppResult


@pytest.mark.asyncio
async def test_html_tool_result_keeps_user_context_and_embed():
    response = HTMLResponse(
        '<section>rendered</section>',
        headers={'Content-Disposition': 'inline'},
    )

    result, files, embeds = await process_tool_result(
        object(), 'chart', (response, {'summary': 'chart is ready'}), 'builtin'
    )

    assert JSONCodec.loads(result) == {'summary': 'chart is ready'}
    assert files == []
    assert embeds == ['<section>rendered</section>']


@pytest.mark.asyncio
async def test_mcp_app_result_extracts_context_and_preserves_embed():
    app_result = MCPAppResult(
        embed={'html': '<main>app</main>'},
        context=[{'type': 'text', 'text': '{"answer":"from app"}'}],
    )

    result, files, embeds = await process_tool_result(
        object(), 'mcp_tool', app_result, 'mcp'
    )

    assert JSONCodec.loads(result) == {'answer': 'from app'}
    assert files == []
    assert embeds == [{'html': '<main>app</main>'}]


@pytest.mark.asyncio
async def test_external_inline_html_keeps_embed_and_returns_success_context():
    result, files, embeds = await process_tool_result(
        object(),
        'external_chart',
        ('<section>external</section>', {
            'Content-Disposition': 'inline',
            'Content-Type': 'text/html',
        }),
        'external',
    )

    assert JSONCodec.loads(result)['status'] == 'success'
    assert files == []
    assert embeds == ['<section>external</section>']


def test_middleware_keeps_legacy_tool_result_import():
    from open_webui.utils.middleware import process_tool_result as legacy_process_tool_result

    assert legacy_process_tool_result is process_tool_result


def test_nested_image_data_uri_is_removed_from_model_text_and_attached_as_file():
    from open_webui.utils.chat_runtime.tool_results import extract_base64_images

    image = 'data:image/png;base64,aGVsbG8='
    files = []

    result = extract_base64_images({'items': [image]}, files)

    assert result == {'items': ['[image]']}
    assert files == [{'type': 'image', 'url': image}]
