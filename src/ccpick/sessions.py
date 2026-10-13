"""Read Claude Code sessions from SSH hosts and attach to them."""

import json
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, Optional

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
# Prints "{}" when the host has no cswap, so ccpick falls back to plain `claude`.
ACCOUNTS_CMD = "command -v cswap >/dev/null && cswap list --json || echo '{}'"


class Account(NamedTuple):
    slot: int  # cswap account number; 0 means no cswap, use plain `claude`
    label: str  # cswap alias or email
    active: bool  # the host's default login
    usage: str  # e.g. "5h 45%  7d 99%"


class Session(NamedTuple):
    host: str
    account: Account
    id: str  # "" for an unreachable host
    state: str
    name: str
    cwd: str
    started: int  # ms since epoch


NO_CSWAP = Account(0, "", True, "")


def remote(cmd: str) -> str:
    # Run through the remote login shell so PATH and proxy settings from the profile apply.
    return f"exec $SHELL -lic {shlex.quote(cmd)}"


def claude_cmd(account: Account, args: str) -> str:
    """`claude <args>` as this account: through `cswap run` when the host has cswap."""
    return f"cswap run {account.slot} -- {args}" if account.slot else f"claude {args}"


def parse_json(out: str):
    """The JSON value in out, skipping any banner the profile prints before it."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("[", "{")):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                return None
    return None


def run_json(host: str, cmd: str):
    """Parsed JSON from cmd on host, or None if ssh fails or prints no JSON."""
    try:
        r = subprocess.run(["ssh", *SSH_OPTS, host, remote(cmd)], capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None
    return parse_json(r.stdout)


def usage_text(u: dict) -> str:
    windows = (("5h", "fiveHour"), ("7d", "sevenDay"))
    return "  ".join(f"{name} {(u.get(k) or {})['pct']:.0f}%" for name, k in windows
                     if (u.get(k) or {}).get("pct") is not None)


def to_accounts(data) -> Optional[list]:
    """Accounts from `cswap list --json`; [NO_CSWAP] without cswap; None if the host is unreachable."""
    if not isinstance(data, dict):
        return None
    accounts = data.get("accounts") or []
    return [Account(a["number"], a.get("alias") or a.get("email", ""), bool(a.get("active")),
                    usage_text(a.get("usage") or {})) for a in accounts] or [NO_CSWAP]


def to_sessions(host: str, account: Account, data) -> list:
    """Attachable (background) sessions of one account on one host, newest first."""
    bg = [s for s in data or [] if s.get("kind") == "background" and s.get("id")]
    return [Session(host, account, s["id"], s.get("state", "?"), s.get("name", ""), s.get("cwd", ""),
                    s.get("startedAt", 0))
            for s in sorted(bg, key=lambda s: -s.get("startedAt", 0))]


def gather(hosts: list):
    """(sessions, {host: accounts or None}) for all hosts, queried in parallel."""
    with ThreadPoolExecutor() as pool:
        accounts = dict(zip(hosts, pool.map(lambda h: to_accounts(run_json(h, ACCOUNTS_CMD)), hosts)))
        jobs = [(h, a) for h in hosts for a in accounts[h] or []]
        lists = pool.map(lambda j: run_json(j[0], claude_cmd(j[1], "agents --json --all")), jobs)
        sessions = [s for (h, a), data in zip(jobs, lists) for s in to_sessions(h, a, data)]
    down = [Session(h, NO_CSWAP, "", "unreachable", "", "", 0) for h in hosts if accounts[h] is None]
    return sessions + down, accounts


def matches(s: Session, query: str) -> bool:
    text = f"{s.host} {s.account.label} {s.state} {s.name} {s.cwd}".lower()
    return all(word in text for word in query.lower().split())


def attach(host: str, cmd: str) -> None:
    """Run cmd on host in this terminal; reconnect while ssh reports a lost connection (exit 255)."""
    try:
        while subprocess.run(["ssh", "-t", host, remote(cmd)]).returncode == 255:
            print("connection lost, reconnecting... (Ctrl+C to give up)", file=sys.stderr)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
