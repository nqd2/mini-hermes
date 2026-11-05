"""Retrieval policy shared by Mini-Hermes file upload and chat context paths."""


def should_bypass_vector_indexing(use_slim: bool, embedding_engine: str | None, collection_name: str | None) -> bool:
    """Keep standalone chat uploads usable in slim without pgvector or embeddings."""
    return bool(use_slim and not embedding_engine and not collection_name)
