import asyncio

from textual.widgets import DataTable

from ccpick import app, sessions
from ccpick.sessions import (NO_CSWAP, Account, Session, claude_cmd, matches, parse_json,
                             to_accounts, to_sessions)

DATA = [
    {"id": "aaaa1111", "kind": "background", "state": "done", "name": "Old paper",
     "cwd": "/w/old", "startedAt": 1},
    {"id": "bbbb2222", "kind": "background", "state": "working", "name": "New analysis",
     "cwd": "/w/new", "startedAt": 2},
    {"pid": 7, "kind": "interactive", "status": "idle", "name": "In a terminal", "cwd": "/w/t"},
]
CSWAP = {"accounts": [
    {"number": 1, "email": "me@work.org", "active": True,
     "usage": {"fiveHour": {"pct": 45.0}, "sevenDay": {"pct": 99.2}}},
    {"number": 2, "email": "me@home.net", "alias": "home", "active": False, "usage": {}},
]}
WORK = Account(1, "me@work.org", True, "5h 45%  7d 99%")
HOME = Account(2, "home", False, "")


def test_parse_json_skips_profile_banner():
    assert parse_json('Welcome to the cluster\n[\n  {"id": "x"}\n]\n') == [{"id": "x"}]
    assert parse_json('motd\n{"accounts": []}') == {"accounts": []}


def test_parse_json_without_json_is_none():
    assert parse_json("bash: claude: command not found\n") is None
    assert parse_json("[ not json") is None


def test_to_accounts_reads_cswap_alias_and_usage():
    assert to_accounts(CSWAP) == [WORK, HOME]


def test_to_accounts_without_cswap_or_host():
    assert to_accounts({}) == [NO_CSWAP]
    assert to_accounts(None) is None


def test_claude_cmd_goes_through_cswap_when_present():
    assert claude_cmd(HOME, "attach ab12") == "cswap run 2 -- attach ab12"
    assert claude_cmd(NO_CSWAP, "agents") == "claude agents"


def test_to_sessions_keeps_background_newest_first():
    got = to_sessions("h", WORK, DATA)
    assert [s.id for s in got] == ["bbbb2222", "aaaa1111"]
    assert got[0] == Session("h", WORK, "bbbb2222", "working", "New analysis", "/w/new", 2)
    assert to_sessions("h", WORK, None) == []


def test_gather_lists_every_account_and_marks_unreachable_hosts(monkeypatch):
    replies = {("h", sessions.ACCOUNTS_CMD): CSWAP, ("h", "cswap run 1 -- agents --json --all"): DATA,
               ("h", "cswap run 2 -- agents --json --all"): DATA[:1]}
    monkeypatch.setattr(sessions, "run_json", lambda host, cmd: replies.get((host, cmd)))
    got, accounts = sessions.gather(["h", "down"])
    assert [(s.host, s.account.slot, s.id) for s in got] == [
        ("h", 1, "bbbb2222"), ("h", 1, "aaaa1111"), ("h", 2, "aaaa1111"), ("down", 0, "")]
    assert accounts == {"h": [WORK, HOME], "down": None}


def test_matches_requires_every_word():
    s = Session("gpu1", HOME, "id", "done", "Old paper", "/w/old", 1)
    assert matches(s, "gpu1 HOME paper")
    assert not matches(s, "gpu1 analysis")


def test_list_mode_prints_tab_separated_rows(monkeypatch, capsys):
    monkeypatch.setattr(app, "gather", lambda hosts: (to_sessions("h", HOME, DATA), {}))
    assert app.main(["--list", "h"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "h\thome\tbbbb2222\tworking\tNew analysis\t/w/new"


def test_tui_filters_attaches_and_asks_for_the_account(monkeypatch):
    rows = to_sessions("h", WORK, DATA) + to_sessions("h", HOME, DATA[:1])
    rows.append(Session("down", NO_CSWAP, "", "unreachable", "", "", 0))
    monkeypatch.setattr(app, "gather", lambda hosts: (rows, {"h": [WORK, HOME], "down": None}))
    calls = []

    async def run():
        picker = app.Picker(["h", "down"])
        picker.run_remote = lambda host, cmd: calls.append((host, cmd))
        async with picker.run_test() as pilot:
            await picker.workers.wait_for_complete()
            await pilot.pause()
            table = picker.query_one(DataTable)
            assert table.row_count == 4
            await pilot.press(*"home paper", "enter")  # attach as the home account
            await pilot.press(*["backspace"] * 10, *"down", "enter")  # unreachable: nothing runs
            await pilot.press(*["backspace"] * 4, "ctrl+n")  # account picker opens
            await pilot.press("down", "enter")
            await pilot.pause()
            await pilot.press("escape")

    asyncio.run(run())
    assert calls == [("h", "cswap run 2 -- attach aaaa1111"), ("h", "cswap run 2 -- agents")]
