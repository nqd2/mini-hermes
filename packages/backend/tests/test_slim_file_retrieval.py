from open_webui.mini_hermes.retrieval import should_bypass_vector_indexing


def test_slim_chat_upload_without_embedding_skips_vector_indexing():
    assert should_bypass_vector_indexing(True, '', None) is True


def test_slim_knowledge_upload_without_embedding_still_requires_vector_indexing():
    assert should_bypass_vector_indexing(True, '', 'knowledge-1') is False


def test_configured_embedding_engine_keeps_vector_indexing():
    assert should_bypass_vector_indexing(True, 'openai', None) is False
