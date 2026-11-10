import pytest
from app.vector.indexer import ensure_index, search_semantic


def test_vector_indexing_and_pelmeni_query():
    count = ensure_index()
    assert count > 0, "Vector database should contain indexed places"

    results = search_semantic("пельмени", limit=5)
    assert len(results) > 0, "Semantic search for 'пельмени' should return matching places"

    first = results[0]
    assert "name" in first
    assert "id" in first
    assert "match_score" in first
    assert first["match_score"] > 0
