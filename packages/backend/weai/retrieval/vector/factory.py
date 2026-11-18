from threading import Lock

from fastapi import HTTPException
from weai.config import (
    ENABLE_MILVUS_MULTITENANCY_MODE,
    ENABLE_QDRANT_MULTITENANCY_MODE,
    VECTOR_DB,
)
from weai.env import USE_SLIM
from weai.retrieval.vector.main import VectorDBBase
from weai.retrieval.vector.type import VectorType


class Vector:
    @staticmethod
    def get_vector(vector_type: str) -> VectorDBBase:
        """
        get vector db instance by vector type
        """
        if USE_SLIM and vector_type != VectorType.PGVECTOR:
            raise HTTPException(
                503,
                'Slim requires PostgreSQL/pgvector for vector storage. Set VECTOR_DB=pgvector and PGVECTOR_DB_URL, or use the standard image.',
            )
        match vector_type:
            case VectorType.MILVUS:
                if ENABLE_MILVUS_MULTITENANCY_MODE:
                    from weai.retrieval.vector.dbs.milvus_multitenancy import (
                        MilvusClient,
                    )

                    return MilvusClient()
                else:
                    from weai.retrieval.vector.dbs.milvus import MilvusClient

                    return MilvusClient()
            case VectorType.QDRANT:
                if ENABLE_QDRANT_MULTITENANCY_MODE:
                    from weai.retrieval.vector.dbs.qdrant_multitenancy import (
                        QdrantClient,
                    )

                    return QdrantClient()
                else:
                    from weai.retrieval.vector.dbs.qdrant import QdrantClient

                    return QdrantClient()
            case VectorType.PINECONE:
                from weai.retrieval.vector.dbs.pinecone import PineconeClient

                return PineconeClient()
            case VectorType.S3VECTOR:
                from weai.retrieval.vector.dbs.s3vector import S3VectorClient

                return S3VectorClient()
            case VectorType.OPENSEARCH:
                from weai.retrieval.vector.dbs.opensearch import OpenSearchClient

                return OpenSearchClient()
            case VectorType.PGVECTOR:
                from weai.retrieval.vector.dbs.pgvector import PgvectorClient

                return PgvectorClient()
            case VectorType.OPENGAUSS:
                from weai.retrieval.vector.dbs.opengauss import OpenGaussClient

                return OpenGaussClient()
            case VectorType.MARIADB_VECTOR:
                from weai.retrieval.vector.dbs.mariadb_vector import (
                    MariaDBVectorClient,
                )

                return MariaDBVectorClient()
            case VectorType.ELASTICSEARCH:
                from weai.retrieval.vector.dbs.elasticsearch import (
                    ElasticsearchClient,
                )

                return ElasticsearchClient()
            case VectorType.CHROMA:
                from weai.retrieval.vector.dbs.chroma import ChromaClient

                return ChromaClient()
            case VectorType.ORACLE23AI:
                from weai.retrieval.vector.dbs.oracle23ai import Oracle23aiClient

                return Oracle23aiClient()
            case VectorType.WEAVIATE:
                from weai.retrieval.vector.dbs.weaviate import WeaviateClient

                return WeaviateClient()
            case VectorType.VALKEY:
                from weai.retrieval.vector.dbs.valkey import ValkeyClient

                return ValkeyClient()
            case _:
                raise ValueError(f'Unsupported vector type: {vector_type}')


VECTOR_DB_CLIENT = None if USE_SLIM else Vector.get_vector(VECTOR_DB)
_vector_client_lock = Lock()


def get_vector_db_client() -> VectorDBBase:
    """Initialize slim's remote client on first use so chat can start without it."""
    global VECTOR_DB_CLIENT
    if VECTOR_DB_CLIENT is not None:
        return VECTOR_DB_CLIENT
    with _vector_client_lock:
        if VECTOR_DB_CLIENT is None:
            from weai import config

            if VECTOR_DB == VectorType.PGVECTOR and not config.PGVECTOR_DB_URL.startswith('postgres'):
                raise HTTPException(503, 'Configure PGVECTOR_DB_URL for remote vector storage.')
            try:
                VECTOR_DB_CLIENT = Vector.get_vector(VECTOR_DB)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    503, f'Unable to connect to configured vector database ({VECTOR_DB}): {exc}'
                ) from exc
    return VECTOR_DB_CLIENT
