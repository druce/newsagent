"""Smoke tests for lib/steps/gui.py — the pipeline GUI HTTP server."""
from contextlib import contextmanager

import httpx

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.gui import start_server

SID = "2026-11-21-07-00-30"


def _seed(db):
    init_db(db)
    st = NewsletterAgentState(session_id=SID, db_path=db)
    st.headline_data = [{"title": "Hello", "rating": 0.5}]
    st.start_step("start")
    st.complete_step("start", message="loaded 3 sources")
    st.save_checkpoint("start")


@contextmanager
def _server(db, runs):
    server = start_server(db, 0, str(runs))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_page_and_session_api(tmp_db, tmp_path):
    _seed(tmp_db)
    with _server(tmp_db, tmp_path) as base:
        page = httpx.get(base + "/")
        assert page.status_code == 200 and "newsagent" in page.text
        assert "const DEFAULT_SID = null;" in page.text  # page picks newest itself
        assert "__DEFAULT_SESSION__" not in page.text
        assert httpx.get(base + "/api/sessions").json()[0]["session_id"] == SID
        ov = httpx.get(base + "/api/session", params={"sid": SID}).json()
        assert ov["n_complete"] == 1 and ov["steps"][0]["status_message"] == "loaded 3 sources"


def test_step_tabs_and_errors(tmp_db, tmp_path):
    _seed(tmp_db)
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "start.log").write_text("hi\n")
    with _server(tmp_db, tmp_path) as base:
        get = lambda p, **q: httpx.get(base + p, params=q)
        assert get("/api/step", sid=SID, step="start", tab="doc").json()["path"] == "skills/start/SKILL.md"
        assert get("/api/step", sid=SID, step="start", tab="code").json()["files"][0]["path"] == "lib/steps/start.py"
        assert get("/api/step", sid=SID, step="start", tab="state").json()["available"] is True
        assert get("/api/log", sid=SID, step="start", offset=0).json()["text"] == "hi\n"
        assert get("/api/step", sid=SID, step="nope", tab="doc").status_code == 400
        assert get("/api/step", sid=SID, step="start", tab="zzz").status_code == 400
        assert get("/api/session", sid="missing").status_code == 400
        assert get("/nothing").status_code == 404


def test_actions(tmp_db, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from lib.steps import gui as gui_mod
    _seed(tmp_db)
    with _server(tmp_db, tmp_path) as base:
        post = lambda body: httpx.post(base + "/api/action", json=body)
        # run_next needs a connected session
        r = post({"sid": SID, "action": "run_next"})
        assert r.status_code == 400 and "no Claude Code session" in r.json()["error"]
        # stop: SIGTERMs the PID in the running marker and tells the agent
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        (tmp_path / SID).mkdir()
        (tmp_path / SID / "gather.running").write_text(f"{proc.pid}\n")
        r = post({"sid": SID, "action": "stop"}).json()
        assert r["killed"] is True and r["step"] == "gather"
        assert proc.wait(5) != 0
        assert httpx.get(base + "/api/chat").json()["messages"][-1]["text"] == "■ Stop"
        # reset: runs lib.steps.start (stubbed here) and reports the new sid
        monkeypatch.setattr(gui_mod._GuiHandler, "_new_session", lambda self: "NEW-SID")
        assert post({"sid": SID, "action": "reset"}).json() == {"sid": "NEW-SID"}
        assert post({"sid": SID, "action": "bogus"}).status_code == 400
        assert os.path.exists(tmp_path / SID / "gather.running")  # marker belongs to the step


def test_command_content_mentions_skill():
    from lib.steps.gui import command_content
    label, content = command_content("run_next", "S1", "cluster")
    assert label == "▶ Run next · cluster" and "newsagent:cluster" in content and "--session S1" in content
    assert "--resume S1" in command_content("run_all", "S1", "cluster")[1]


def test_output_artifacts_and_file_routes(tmp_db, tmp_path):
    _seed(tmp_db)
    runs = tmp_path / SID
    runs.mkdir()
    (runs / "start.json").write_text('{"sources": 3}')
    (runs / "start.log").write_text("<script>alert(1)</script>\n")
    with _server(tmp_db, tmp_path) as base:
        get = lambda p, **q: httpx.get(base + p, params=q)
        assert get("/api/step", sid=SID, step="start", tab="output").json()["data"] == {"sources": 3}
        # runs_root here is tmp_path (absolute), files are listed relative to the repo root
        groups = get("/api/step", sid=SID, step="start", tab="artifacts").json()["groups"]
        rel = [f["path"] for g in groups for f in g["files"]]
        assert any(p.endswith("start.json") for p in rel)
        log_rel = next(p for p in rel if p.endswith("start.log"))
        r = get("/api/file", sid=SID, step="start", path=log_rel)
        assert r.status_code == 200 and r.headers["content-security-policy"].startswith("sandbox;")
        assert r.headers["content-type"].startswith("text/plain")
        assert get("/api/file", sid=SID, step="start", path="pyproject.toml").status_code == 400


def test_bridge_owner(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from lib.steps.gui import bridge_owner, write_bridge_file
    f = tmp_path / "bridge.json"
    assert bridge_owner(f) is None                       # no file
    write_bridge_file(1, "t", f)
    assert bridge_owner(f) is None                       # our own pid
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        f.write_text(json.dumps({"url": "x", "token": "t", "pid": proc.pid}))
        assert bridge_owner(f) == proc.pid               # another live server
    finally:
        proc.kill(); proc.wait()
    assert bridge_owner(f) is None                       # dead owner → free
    assert os.getpid() != proc.pid
