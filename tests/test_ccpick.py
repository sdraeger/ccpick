import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from textual.widgets import DataTable

from ccpick import app, sessions
from ccpick.sessions import (NO_CSWAP, Account, Session, claude_cmd, matches, parse_json,
                             to_accounts, to_past, to_sessions)

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
    replies[("h", "python3 -")] = [{"account": "default", "sessionId": "cccc3333-x", "title": "Old chat",
                                    "cwd": "/w/c", "mtime": 5}]
    monkeypatch.setattr(sessions, "run_json", lambda host, cmd, input=None: replies.get((host, cmd)))
    got, accounts = sessions.gather(["h", "down"])
    assert [(s.host, s.account.slot, s.id, s.state) for s in got] == [
        ("h", 1, "bbbb2222", "working"), ("h", 1, "aaaa1111", "done"), ("h", 2, "aaaa1111", "done"),
        ("h", 1, "cccc3333-x", "past"), ("down", 0, "", "unreachable")]
    assert accounts == {"h": [WORK, HOME], "down": None}


def test_switch_runs_cswap_switch_and_reports_failures(monkeypatch):
    seen = []

    class R:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, "bash: no job control in this shell\n"

    replies = iter([R(0, "Switched to me@home.net\n"), R(1, "Error: account 2 needs re-login\n")])
    monkeypatch.setattr(sessions.subprocess, "run", lambda args, **k: seen.append(args[-1]) or next(replies))
    assert sessions.switch("h", HOME) == (True, "Switched to me@home.net")
    assert sessions.switch("h", HOME) == (False, "Error: account 2 needs re-login")
    assert "cswap switch 2" in seen[0]


def test_to_past_maps_accounts_and_skips_running_and_duplicates():
    data = [
        {"account": "default", "sessionId": "aaaa1111-run", "title": "running", "cwd": "/a", "mtime": 9},
        {"account": "default", "sessionId": "dddd4444-x", "title": "mine", "cwd": "/d", "mtime": 3},
        {"account": "2", "sessionId": "eeee5555-x", "title": "home chat", "cwd": "/e", "mtime": 7},
        {"account": "1", "sessionId": "ffff6666-x", "title": "old profile of the default", "mtime": 8},
        {"account": "2", "sessionId": "dddd4444-x", "title": "copy", "cwd": "/d", "mtime": 1},
    ]
    got = to_past("h", [WORK, HOME], data, {"aaaa1111"})
    assert [(s.account.slot, s.id, s.name) for s in got] == [(2, "eeee5555-x", "home chat"),
                                                             (1, "dddd4444-x", "mine")]
    assert to_past("h", [NO_CSWAP], data[1:2], set())[0].account == NO_CSWAP


def transcript(path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_history_script_reads_titles_and_cswap_profiles(tmp_path):
    home = tmp_path
    proj = home / ".claude" / "projects" / "-w-a"
    transcript(proj / "s1.jsonl", {"type": "user", "cwd": "/w/a"}, {"type": "ai-title", "aiTitle": "AI"},
               {"type": "custom-title", "customTitle": "Mine"})
    transcript(proj / "s2.jsonl", {"type": "user", "cwd": "/w/a"},
               {"type": "last-prompt", "lastPrompt": "fix the loader"})
    transcript(proj / "s3.jsonl", {"type": "user", "cwd": "/w/a"}, {"type": "continued-in"})
    profile = home / ".local" / "share" / "claude-swap" / "sessions" / "2-me_home.net"
    transcript(profile / "projects" / "-w-b" / "s4.jsonl", {"type": "user", "cwd": "/w/b"})
    shared = home / ".claude-swap-backup" / "sessions" / "3-x"
    shared.mkdir(parents=True)
    (shared / "projects").symlink_to(home / ".claude" / "projects")  # shared history: no duplicates
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "XDG_DATA_HOME")}
    out = subprocess.run([sys.executable, Path(sessions.__file__).with_name("history.py")],
                         env=dict(env, HOME=str(home)), capture_output=True, text=True, check=True).stdout
    got = sorted((c["account"], c["sessionId"], c["title"], c["cwd"]) for c in json.loads(out))
    assert got == [("2", "s4", "(untitled)", "/w/b"), ("default", "s1", "Mine", "/w/a"),
                   ("default", "s2", "fix the loader", "/w/a")]


def test_resume_background_runs_in_the_cwd_and_reads_the_short_id(monkeypatch):
    seen = []

    class R:
        returncode, stderr = 0, ""
        stdout = "backgrounded \u00b7 43cd2455 (idle)\n  claude attach 43cd2455    open in this terminal\n"

    monkeypatch.setattr(sessions.subprocess, "run", lambda args, **k: seen.append(args[-1]) or R())
    past = Session("h", HOME, "43cd2455-full", "past", "chat", "/w/my dir", 1)
    assert sessions.resume_background(past) == ("43cd2455", "  claude attach 43cd2455    open in this terminal")
    assert shlex.split(seen[0])[-1] == "cd '/w/my dir' && cswap run 2 -- --bg --resume 43cd2455-full"


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
    rows.append(Session("h", WORK, "cccc3333-full", "past", "Old chat", "/w/c", 0))
    rows.append(Session("down", NO_CSWAP, "", "unreachable", "", "", 0))
    monkeypatch.setattr(app, "gather", lambda hosts: (rows, {"h": [WORK, HOME], "down": None}))
    calls = []

    async def run():
        picker = app.Picker(["h", "down"])
        picker.run_remote = lambda host, cmd: calls.append((host, cmd))
        picker.swap = lambda host, a: calls.append(("swap", host, a.slot))
        picker.resume = lambda s: calls.append(("resume", s.id))
        async with picker.run_test() as pilot:
            await picker.workers.wait_for_complete()
            await pilot.pause()
            table = picker.query_one(DataTable)
            assert table.row_count == 5
            await pilot.press(*"home paper", "enter")  # attach as the home account
            await pilot.press(*["backspace"] * 10, *"down", "enter")  # unreachable: nothing runs
            await pilot.press(*["backspace"] * 4, "ctrl+n")  # account picker opens
            await pilot.press("down", "enter")
            await pilot.pause()
            await pilot.press("ctrl+s", "enter")  # swap preselects the non-default account
            await pilot.pause()
            await pilot.press(*"old chat", "enter")  # past conversation: resume in the background
            await pilot.pause()
            await pilot.press("escape")

    asyncio.run(run())
    assert calls == [("h", "cswap run 2 -- attach aaaa1111"), ("h", "cswap run 2 -- agents"),
                     ("swap", "h", 2), ("resume", "cccc3333-full")]
