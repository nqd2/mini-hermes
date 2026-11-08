from open_webui.utils.chat_runtime.stream_tags import TagOutputTransformer


def _transformer():
    return TagOutputTransformer(id_factory=lambda prefix: f'{prefix}_test', clock=lambda: 100.0)


def _message(text):
    return {
        'type': 'message',
        'id': 'msg_test',
        'status': 'in_progress',
        'role': 'assistant',
        'content': [{'type': 'output_text', 'text': text}],
    }


def _text(item):
    return item['content'][-1]['text']


def test_converts_reasoning_tag_into_completed_reasoning_item_and_preserves_tail():
    output = [_message('Visible <think type="analysis">Reason it out</think> answer')]

    updated, ended = _transformer().transform('reasoning', [('<think>', '</think>')], output)

    assert updated is output
    assert ended is True
    assert output[0]['type'] == 'message'
    assert _text(output[0]) == 'Visible '
    assert output[1] == {
        'type': 'reasoning',
        'id': 'r_test',
        'status': 'completed',
        'start_tag': '<think>',
        'end_tag': '</think>',
        'attributes': {'type': 'analysis'},
        'content': [{'type': 'output_text', 'text': 'Reason it out'}],
        'summary': None,
        'started_at': 100.0,
        'ended_at': 100.0,
        'duration': 0,
    }
    assert _text(output[2]) == 'answer'


def test_detects_start_tag_split_across_stream_deltas():
    transformer = _transformer()
    output = [_message('Visible <thi')]

    updated, ended = transformer.transform('reasoning', [('<think>', '</think>')], output)
    assert updated is None
    assert ended is False

    output[0]['content'][0]['text'] += 'nk>Hidden</think> tail'
    updated, ended = transformer.transform('reasoning', [('<think>', '</think>')], output)

    assert updated is output
    assert ended is True
    assert output[1]['type'] == 'reasoning'
    assert output[1]['content'] == [{'type': 'output_text', 'text': 'Hidden'}]
    assert _text(output[2]) == 'tail'


def test_converts_code_interpreter_tag_with_language_attribute():
    output = [_message('Before <code lang="javascript">console.log(1)</code> after')]

    updated, ended = _transformer().transform('code_interpreter', [('<code>', '</code>')], output)

    assert updated is output
    assert ended is True
    assert output[1]['type'] == 'open_webui:code_interpreter'
    assert output[1]['lang'] == 'javascript'
    assert output[1]['code'] == 'console.log(1)'
    assert output[1]['attributes'] == {'lang': 'javascript'}
    assert _text(output[2]) == 'after'


def test_detects_end_tag_split_across_stream_deltas():
    transformer = _transformer()
    output = [
        {
            'type': 'reasoning',
            'id': 'reasoning_test',
            'status': 'in_progress',
            'start_tag': '<think>',
            'end_tag': '</think>',
            'content': [{'type': 'output_text', 'text': 'Hidden</thi'}],
            'started_at': 90.0,
        }
    ]

    updated, ended = transformer.transform('reasoning', [('<think>', '</think>')], output)
    assert updated is None
    assert ended is False

    output[0]['content'][0]['text'] += 'nk> visible'
    updated, ended = transformer.transform('reasoning', [('<think>', '</think>')], output)

    assert updated is output
    assert ended is True
    assert output[0]['content'] == [{'type': 'output_text', 'text': 'Hidden'}]
    assert output[0]['status'] == 'completed'
    assert _text(output[1]) == 'visible'


def test_supports_non_angle_tags_without_capture_groups():
    output = [_message('Visible STARTsolutionEND answer')]

    updated, ended = _transformer().transform('solution', [('START', 'END')], output)

    assert updated is output
    assert ended is True
    assert _text(output[0]) == 'Visible '
    assert _text(output[1]) == 'solution'
    assert _text(output[2]) == 'answer'


def test_removes_empty_reasoning_block_and_preserves_following_text():
    output = [_message('<think></think> Visible answer')]

    updated, ended = _transformer().transform('reasoning', [('<think>', '</think>')], output)

    assert updated is output
    assert ended is True
    assert len(output) == 1
    assert output[0]['type'] == 'message'
    assert _text(output[0]) == 'Visible answer'
