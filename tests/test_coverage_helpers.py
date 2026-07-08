from lib.state import NewsletterAgentState
from lib.steps.coverage import (
    _candidates,
    _candidate_text,
    _shortlist_pairs,
    _build_pairs,
    _group_counts,
)


def _state(headlines):
    s = NewsletterAgentState(session_id="t", db_path=":memory:")
    s.headline_data = headlines
    return s


def test_candidates_skip_missing_short_summary():
    s = _state([
        {"title": "A", "url": "a", "short_summary": "sa"},
        {"title": "B", "url": "b"},                       # no short_summary
        {"title": "C", "url": "c", "short_summary": "  "},  # blank
        {"title": "D", "url": "d", "short_summary": "sd"},
    ])
    idxs = [i for i, _ in _candidates(s)]
    assert idxs == [0, 3]


def test_candidate_text_joins_title_and_short_summary():
    assert _candidate_text({"title": "T", "short_summary": "S"}) == "T\nS"


def test_shortlist_pairs_upper_triangle_by_threshold():
    # v0 == v1 (cosine 1.0); v2 orthogonal to both.
    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    assert _shortlist_pairs(vecs, 0.7) == [(0, 1)]


def test_build_pairs_uses_global_indices_and_full_summary():
    s = _state([
        {"title": "A", "url": "a", "short_summary": "sa", "summary": "- full a"},
        {"title": "B", "url": "b", "short_summary": "sb", "summary": "- full b"},
    ])
    pairs = _build_pairs(s, cand_index=[0, 1], shortlist=[(0, 1)])
    assert len(pairs) == 1
    p = pairs[0]
    assert p["id"] == "0-1"
    assert p["a_summary"] == "- full a"
    assert p["b_summary"] == "- full b"


def test_group_counts_transitive_component():
    # candidates global indices 0,1,2,5 ; confirmed 0-1 and 1-2 → {0,1,2} size 3
    counts = _group_counts([0, 1, 2, 5], same_pair_ids={"0-1", "1-2"})
    assert counts == {0: 3, 1: 3, 2: 3, 5: 1}


def test_group_counts_all_singletons_when_no_pairs():
    counts = _group_counts([0, 1, 2], same_pair_ids=set())
    assert counts == {0: 1, 1: 1, 2: 1}
