"""Tests for evals.run.rate_decisions — Decisions-API vs gpt-4o-mini rate_* eval."""
import json
import subprocess
import sys

import pytest

from evals.run import rate_decisions as R


def test_auc_perfect_inverse_and_ties():
    assert R.auc([0.9, 0.8, 0.1], [True, True, False]) == 1.0
    assert R.auc([0.1, 0.2, 0.9], [True, True, False]) == 0.0
    assert R.auc([0.5, 0.5], [True, False]) == 0.5


def test_brier_logloss_accuracy():
    assert R.brier([1.0, 0.0], [True, False]) == 0.0
    assert R.accuracy([0.6, 0.4], [True, True]) == 0.5
    assert R.log_loss([1.0], [True]) == pytest.approx(0.0)
    assert R.log_loss([0.0], [True]) == pytest.approx(-__import__("math").log(1e-3))


def test_every_question_carries_the_no_content_rule():
    assert set(R.AXES) == {"quality_low", "importance", "on_topic"}
    assert all(R.NO_CONTENT in R.QUESTIONS[a]
               for a in R.AXES)
    assert "even when it cites real revenue" in R.QUESTIONS["quality_low"]


def _cases(n):
    return [{"case_id": f"c{i}", "id": f"a{i}", "input_text": f"T{i}\n- s{i}", "sid": "s", "from_axis": "on_topic"}
            for i in range(n)]


def test_gold_batches_pass_check_batch_and_read_back(tmp_path):
    paths = R.write_gold_batches(_cases(30), tmp_path)
    assert len(paths) == 2
    (tmp_path / "opus-results").mkdir()
    for p in paths:
        ids = json.loads(p.read_text())["ids"]
        res = {"results": [{"id": i, "quality_low": False, "importance": True, "on_topic": True} for i in ids]}
        (tmp_path / "opus-results" / p.name).write_text(json.dumps(res))
        ok = subprocess.run([sys.executable, "tools/check_batch.py", str(tmp_path / "opus-results" / p.name)])
        assert ok.returncode == 0
    gold, problems = R.read_gold(tmp_path)
    assert problems == [] and len(gold) == 30 and gold["a0"]["importance"] is True


def test_read_gold_flags_non_boolean(tmp_path):
    (p,) = R.write_gold_batches(_cases(2), tmp_path)
    (tmp_path / "opus-results").mkdir()
    (tmp_path / "opus-results" / p.name).write_text(json.dumps({"results": [
        {"id": "a0", "quality_low": False, "importance": True, "on_topic": True},
        {"id": "a1", "quality_low": "no", "importance": True, "on_topic": True}]}))
    gold, problems = R.read_gold(tmp_path)
    assert list(gold) == ["a0"] and "a1" in problems[0]


def test_eval_questions_mirror_production_prompts():
    import lib.prompts  # noqa: F401
    from lib.llm import get_prompt
    for axis, name in (("quality_low", "rate_quality"), ("importance", "rate_importance"),
                       ("on_topic", "rate_on_topic")):
        assert R.QUESTIONS[axis] == get_prompt(name).system_prompt, name
