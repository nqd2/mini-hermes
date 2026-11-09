from open_webui.utils.chat_runtime.stream_state import combine_stream_outputs


def test_combines_continuation_message_into_prior_item_and_keeps_prior_id():
    prior = [
        {'type': 'reasoning', 'id': 'reasoning-1'},
        {
            'type': 'message',
            'id': 'message-original',
            'role': 'assistant',
            'status': 'in_progress',
            'content': [{'type': 'output_text', 'text': 'First'}],
            'custom': 'preserved',
        },
    ]
    current = [
        {
            'type': 'message',
            'id': 'message-generated',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': ' second'}],
        },
        {'type': 'function_call', 'call_id': 'call-1'},
    ]

    result = combine_stream_outputs(True, prior, current)

    assert result == [
        {'type': 'reasoning', 'id': 'reasoning-1'},
        {
            'type': 'message',
            'id': 'message-original',
            'role': 'assistant',
            'status': 'completed',
            'content': [
                {'type': 'output_text', 'text': 'First'},
                {'type': 'output_text', 'text': ' second'},
            ],
            'custom': 'preserved',
        },
        {'type': 'function_call', 'call_id': 'call-1'},
    ]


def test_does_not_merge_output_when_not_continuing_or_message_items_do_not_match():
    prior = [{'type': 'message', 'id': 'prior', 'role': 'assistant', 'content': []}]
    current = [{'type': 'reasoning', 'id': 'current', 'content': []}]

    assert combine_stream_outputs(False, prior, current) == prior + current
    assert combine_stream_outputs(True, prior, current) == prior + current
    assert combine_stream_outputs(True, [], current) == current
