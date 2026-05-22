from lib.prompts._dispatch_schemas import DraftSectionResult, RewriteResult


def test_draft_section_result_minimal():
    r = DraftSectionResult(
        cat="OpenAI",
        final_section_markdown="## OpenAI\n- ships GPT-6",
        iterations=1,
        scores=[7.5],
        feedbacks=["needs more detail"],
        accepted=False,
    )
    assert r.cat == "OpenAI"
    assert r.iterations == 1
    assert r.accepted is False


def test_rewrite_result_minimal():
    r = RewriteResult(
        final_newsletter_markdown="## OpenAI\n- ships GPT-6\n\n## EU\n- AI Act",
        title="AI Roundup",
        iterations=2,
        scores=[6.0, 8.2],
        feedbacks=["too short", "good"],
        accepted=True,
    )
    assert r.title == "AI Roundup"
    assert r.accepted is True
    assert r.scores == [6.0, 8.2]


def test_dispatch_schemas_serializable():
    r = DraftSectionResult(
        cat="X", final_section_markdown="## X", iterations=0,
        scores=[], feedbacks=[], accepted=False,
    )
    js = r.model_json_schema()
    assert "cat" in js["properties"]
    assert "final_section_markdown" in js["properties"]
