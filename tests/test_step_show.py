from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.show import cli as show_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="x1", db_path=tmp_db,
                                 sources_file="sources.yaml",
                                 sources={"A": {"type": "rss"}})
    state.complete_step("start", message="setup")
    state.start_step("gather")
    state.save_checkpoint("start")
    state.save_checkpoint("gather")


def test_show_dumps_full_state(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db])
    assert result.exit_code == 0
    assert "x1" in result.output
    assert "start" in result.output
    assert "gather" in result.output
    assert "complete" in result.output.lower()


def test_show_specific_step(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db, "--step", "start"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_show_missing_session(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["nope", "--db", tmp_db])
    assert "No state" in result.output or "not found" in result.output.lower()


def test_show_counts_sections_not_selected_articles(tmp_db):
    """newsletter_section_data holds one entry PER ARTICLE; "Sections" must
    report the number of distinct categories, not the article count."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="x2", db_path=tmp_db)
    state.complete_step("start")
    state.newsletter_section_data = [
        {"cat": "AI", "link": "https://e.com/1", "id": 0},
        {"cat": "AI", "link": "https://e.com/2", "id": 1},
        {"cat": "Robots", "link": "https://e.com/3", "id": 2},
    ]
    state.save_checkpoint("start")
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x2", "--db", tmp_db])
    assert result.exit_code == 0, result.output
    assert "Sections:  2" in result.output
    assert "Selected articles: 3" in result.output
