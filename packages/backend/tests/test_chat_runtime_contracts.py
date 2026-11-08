from open_webui.utils.chat_runtime.stream_state import update_assistant_message_from_stream
from open_webui.utils.chat_runtime.tool_results import _is_tool_result_error, build_terminal_file_tool_result


def test_stream_accumulates_content_and_completion_without_changing_wire_shape():
    message = {}
    assert not update_assistant_message_from_stream(message, 'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n')
    assert not update_assistant_message_from_stream(message, b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}\n\n')
    assert update_assistant_message_from_stream(message, 'data: [DONE]\n\n')
    assert message['content'] == 'hello world'
    assert message['finish_reason'] == 'stop'


def test_uploaded_viewer_is_not_converted_to_terminal_file():
    assert build_terminal_file_tool_result('display_file', {'path': '/file.xlsx'}, {'exists': True}, {}, {}) is None


def test_legacy_tool_error_contract():
    assert _is_tool_result_error('Error: denied')
    assert _is_tool_result_error('{"error":{"message":"denied"}}')
    assert not _is_tool_result_error('{"text":"actual content"}')
