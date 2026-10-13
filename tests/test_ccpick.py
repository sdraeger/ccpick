import asyncio

from textual.widgets import DataTable

from ccpick import app, sessions
from ccpick.sessions import Session, matches, parse_json, to_sessions

DATA = [
    {"id": "aaaa1111", "kind": "background", "state": "done", "name": "Old paper",
     "cwd": "/w/old", "startedAt": 1},
    {"id": "bbbb2222", "kind": "background", "state": "working", "name": "New analysis",
     "cwd": "/w/new", "startedAt": 2},
    {"pid": 7, "kind": "interactive", "status": "idle", "name": "In a terminal", "cwd": "/w/t"},
]


def test_parse_json_skips_profile_banner():
    assert parse_json('Welcome to the cluster\n[\n  {"id": "x"}\n]\n') == [{"id": "x"}]


def test_parse_json_without_json_is_none():
    assert parse_json("bash: claude: command not found\n") is None
    assert parse_json("[ not json") is None


def test_to_sessions_keeps_background_newest_first():
    got = to_sessions("h", DATA)
    assert [s.id for s in got] == ["bbbb2222", "aaaa1111"]
    assert got[0] == Session("h", "bbbb2222", "working", "New analysis", "/w/new", 2)


def test_unreachable_host_gets_one_row_without_id():
    assert to_sessions("h", None) == [Session("h", "", "unreachable", "", "", 0)]


def test_matches_requires_every_word():
    s = Session("gpu1", "id", "done", "Old paper", "/w/old", 1)
    assert matches(s, "gpu1 PAPER")
    assert not matches(s, "gpu1 analysis")


def test_list_mode_prints_tab_separated_rows(monkeypatch, capsys):
    monkeypatch.setattr(app, "gather", lambda hosts: to_sessions(hosts[0], DATA))
    assert app.main(["--list", "h"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "h\tbbbb2222\tworking\tNew analysis\t/w/new"


def test_fetch_failure_is_none(monkeypatch):
    class R:
        stdout = ""
    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: R())
    assert sessions.fetch("h") is None


def test_tui_lists_filters_and_attaches(monkeypatch):
    monkeypatch.setattr(app, "gather", lambda hosts: to_sessions("h", DATA) + to_sessions("down", None))
    calls = []

    async def run():
        picker = app.Picker(["h", "down"])
        picker.run_remote = lambda host, cmd: calls.append((host, cmd))
        async with picker.run_test() as pilot:
            await picker.workers.wait_for_complete()
            await pilot.pause()
            table = picker.query_one(DataTable)
            assert table.row_count == 3
            await pilot.press(*"paper")
            assert table.row_count == 1
            await pilot.press("enter")
            await pilot.press(*["backspace"] * 5, *"down", "enter")
            assert table.row_count == 1
            await pilot.press("ctrl+n")
            await pilot.press("escape")

    asyncio.run(run())
    assert calls == [("h", "claude attach aaaa1111"), ("down", "claude agents")]
