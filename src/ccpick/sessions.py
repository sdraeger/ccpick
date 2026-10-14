"""Read Claude Code sessions from SSH hosts and attach to them."""

import json
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple, Optional

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
# What an interactive shell without a terminal (`$SHELL -lic` over ssh) prints to stderr.
JOB_CONTROL_NOISE = re.compile(r"cannot set terminal process group|no job control in this shell")
# Lists past conversations on the host; see history.py.
HISTORY_SCRIPT = Path(__file__).with_name("history.py").read_text()
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
    id: str  # short background id; full session id for a past conversation; "" if unreachable
    state: str  # agent view state, "past", or "unreachable"
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


def ssh_run(host: str, cmd: str, timeout: int = 30,
            input: Optional[str] = None) -> Optional[subprocess.CompletedProcess]:
    """cmd on host without a terminal, fed input on stdin; None on timeout."""
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(["ssh", *SSH_OPTS, host, remote(cmd)], capture_output=True, text=True,
                              timeout=timeout, **stdin)
    except subprocess.TimeoutExpired:
        return None


def run_json(host: str, cmd: str, input: Optional[str] = None):
    """Parsed JSON from cmd on host, or None if ssh fails or prints no JSON."""
    r = ssh_run(host, cmd, input=input)
    return parse_json(r.stdout) if r else None


def switch(host: str, account: Account) -> tuple:
    """Make account the host's default login with `cswap switch`; (ok, last output line)."""
    r = ssh_run(host, f"cswap switch {account.slot}", timeout=60)
    if r is None:
        return False, "timed out"
    return r.returncode == 0, last_line(r)


def last_line(r: subprocess.CompletedProcess) -> str:
    lines = [line for line in (r.stdout + r.stderr).splitlines()
             if line.strip() and not JOB_CONTROL_NOISE.search(line)]
    return lines[-1] if lines else ""


def resume_background(s: Session) -> tuple:
    """Continue a past conversation as a background session; (short id or None, last output line)."""
    cmd = claude_cmd(s.account, f"--bg --resume {shlex.quote(s.id)}")
    r = ssh_run(s.host, f"cd {shlex.quote(s.cwd)} && {cmd}" if s.cwd else cmd, timeout=120)
    if r is None:
        return None, "timed out"
    m = re.search(r"claude attach (\S+)", r.stdout)
    return (m.group(1) if m else None), last_line(r)


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


def to_past(host: str, accounts: list, data, running: set) -> list:
    """Past conversations on one host, newest first, under the account whose history holds them.

    Skips conversations already running in the background (by short id) and duplicates.
    """
    if not isinstance(data, list):
        return []
    default = next((a for a in accounts if a.active), accounts[0])
    by_slot = {str(a.slot): a for a in accounts if a.slot}
    out, seen = [], set(running)
    for c in sorted(data, key=lambda c: -c.get("mtime", 0)):
        sid, where = c.get("sessionId", ""), c.get("account")
        account = default if where == "default" else by_slot.get(where)
        # `cswap run` uses the default config dir for the default account, not its old profile.
        if account is None or (where != "default" and account.active) or sid[:8] in seen:
            continue
        seen.add(sid[:8])
        out.append(Session(host, account, sid, "past", c.get("title", ""), c.get("cwd", ""),
                           c.get("mtime", 0)))
    return out


def gather(hosts: list):
    """(sessions, {host: accounts or None}) for all hosts, queried in parallel.

    Background sessions come first, then past conversations, then unreachable hosts.
    """
    with ThreadPoolExecutor() as pool:
        accounts = dict(zip(hosts, pool.map(lambda h: to_accounts(run_json(h, ACCOUNTS_CMD)), hosts)))
        up = [h for h in hosts if accounts[h] is not None]
        jobs = [(h, a) for h in up for a in accounts[h]]
        lists = pool.map(lambda j: run_json(j[0], claude_cmd(j[1], "agents --json --all")), jobs)
        histories = pool.map(lambda h: run_json(h, "python3 -", input=HISTORY_SCRIPT), up)
        live = [s for (h, a), data in zip(jobs, lists) for s in to_sessions(h, a, data)]
        past = [s for h, data in zip(up, histories)
                for s in to_past(h, accounts[h], data, {x.id for x in live if x.host == h})]
    down = [Session(h, NO_CSWAP, "", "unreachable", "", "", 0) for h in hosts if accounts[h] is None]
    return live + past + down, accounts


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
