"""
Retriever: Knowledge retrieval for the Background Model.

Provides RAG (Retrieval-Augmented Generation) capabilities to the
Background Model. When S1 delegates a query that requires factual
knowledge, the Retriever searches external knowledge sources and
returns relevant context.

The Retriever operates as one of the parallel ensembles in the
Multi-Background Ensemble architecture — it can run concurrently
with the Reasoner and ToolExecutor.
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import hashlib


@dataclass
class RetrievalResult:
    """A single retrieved document or knowledge snippet.

    Attributes:
        doc_id: Unique identifier for the document.
        content: The retrieved text content.
        score: Relevance score (0-1).
        source: Source of the knowledge (e.g., "wikipedia", "arxiv").
        title: Document title if available.
        url: Source URL if available.
    """
    doc_id: str
    content: str
    score: float
    source: str = "unknown"
    title: Optional[str] = None
    url: Optional[str] = None


@dataclass
class RetrievalResponse:
    """Complete retrieval response with metadata.

    Attributes:
        query: The original search query.
        results: Ranked list of retrieval results.
        total_candidates: Number of documents considered.
        retrieval_time_ms: Time spent on retrieval.
        query_embedding_dim: Dimension of query embedding (for caching).
    """
    query: str
    results: List[RetrievalResult] = field(default_factory=list)
    total_candidates: int = 0
    retrieval_time_ms: float = 0.0
    query_embedding_dim: int = 2048


class Retriever:
    """Knowledge retrieval engine.

    Supports multiple retrieval strategies:
    - Dense retrieval: semantic search using embeddings
    - Sparse retrieval: keyword-based BM25
    - Hybrid: combination of dense + sparse with reranking

    The Retriever can be configured with multiple knowledge sources:
    - Local vector database (FAISS, Chroma, Qdrant)
    - Web search (via API)
    - Custom knowledge bases (ArXiv, Wikipedia, internal docs)
    """

    def __init__(
        self,
        embedding_dim: int = 2048,
        top_k: int = 5,
        retrieval_strategy: str = "hybrid",
        knowledge_sources: Optional[List[str]] = None,
        cache_enabled: bool = True,
    ):
        self.embedding_dim = embedding_dim
        self.top_k = top_k
        self.retrieval_strategy = retrieval_strategy

        if knowledge_sources is None:
            knowledge_sources = ["local", "web"]
        self.knowledge_sources = knowledge_sources

        self.cache_enabled = cache_enabled
        self._cache: Dict[str, RetrievalResponse] = {}

    def retrieve(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        top_k: Optional[int] = None,
    ) -> RetrievalResponse:
        """Retrieve knowledge relevant to the query.

        Args:
            query: The search query (extracted from S1's delegation).
            context: Optional context from S1 for query refinement.
            top_k: Override default top_k.

        Returns:
            RetrievalResponse with ranked results.
        """
        if top_k is None:
            top_k = self.top_k

        # Check cache
        cache_key = self._cache_key(query)
        if self.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        # Query refinement using S1 context
        refined_query = self._refine_query(query, context)

        results = []

        # Dense retrieval
        if self.retrieval_strategy in ("dense", "hybrid"):
            dense_results = self._dense_retrieval(refined_query, top_k * 2)
            results.extend(dense_results)

        # Sparse retrieval
        if self.retrieval_strategy in ("sparse", "hybrid"):
            sparse_results = self._sparse_retrieval(refined_query, top_k * 2)
            results.extend(sparse_results)

        # Deduplicate and rerank
        results = self._deduplicate_and_rerank(results, top_k)

        response = RetrievalResponse(
            query=query,
            results=results,
            total_candidates=len(results),
        )

        # Cache result
        if self.cache_enabled:
            self._cache[cache_key] = response

        return response

    def _refine_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Refine the search query using S1 context.

        The S1 context provides valuable information about what the
        user is discussing, allowing for more targeted retrieval.
        """
        if context is None:
            return query

        # Add conversation context to improve retrieval
        conversation_summary = context.get("conversation_summary", "")
        if conversation_summary:
            # Use the conversation context to disambiguate
            return f"{conversation_summary}\n\nSpecific question: {query}"

        return query

    def _dense_retrieval(
        self, query: str, top_k: int
    ) -> List[RetrievalResult]:
        """Dense (semantic) retrieval using embeddings.

        This is a placeholder that would use a real embedding model
        and vector database in production.
        """
        # Placeholder: return empty results
        return []

    def _sparse_retrieval(
        self, query: str, top_k: int
    ) -> List[RetrievalResult]:
        """Sparse (keyword) retrieval using BM25.

        This is a placeholder that would use a real search engine
        or BM25 index in production.
        """
        # Placeholder: return empty results
        return []

    def _deduplicate_and_rerank(
        self,
        results: List[RetrievalResult],
        top_k: int,
    ) -> List[RetrievalResult]:
        """Deduplicate results by content hash and rerank by score."""
        seen = set()
        unique = []
        for r in sorted(results, key=lambda x: x.score, reverse=True):
            content_hash = hashlib.md5(r.content.encode()).hexdigest()
            if content_hash not in seen:
                seen.add(content_hash)
                unique.append(r)
        return unique[:top_k]

    @staticmethod
    def _cache_key(query: str) -> str:
        """Generate cache key for a query."""
        return hashlib.md5(query.encode()).hexdigest()

    def clear_cache(self) -> None:
        """Clear the retrieval cache."""
        self._cache.clear()
