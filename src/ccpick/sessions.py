"""Read Claude Code sessions from SSH hosts and attach to them."""

import json
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, Optional

LIST_CMD = "claude agents --json --all"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class Session(NamedTuple):
    host: str
    id: str  # "" for an unreachable host
    state: str
    name: str
    cwd: str
    started: int  # ms since epoch


def remote(cmd: str) -> str:
    # Run through the remote login shell so PATH and proxy settings from the profile apply.
    return f"exec $SHELL -lic {shlex.quote(cmd)}"


def parse_json(out: str) -> Optional[list]:
    """The JSON array from `claude agents --json`, skipping any banner the profile prints."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("["):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                return None
    return None


def fetch(host: str) -> Optional[list]:
    """The host's session list, or None if ssh fails or prints no JSON."""
    try:
        r = subprocess.run(["ssh", *SSH_OPTS, host, remote(LIST_CMD)], capture_output=True,
                           text=True, timeout=30, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None
    return parse_json(r.stdout)


def to_sessions(host: str, data: Optional[list]) -> list:
    """Attachable (background) sessions on one host, newest first."""
    if data is None:
        return [Session(host, "", "unreachable", "", "", 0)]
    bg = [s for s in data if s.get("kind") == "background" and s.get("id")]
    return [Session(host, s["id"], s.get("state", "?"), s.get("name", ""), s.get("cwd", ""),
                    s.get("startedAt", 0))
            for s in sorted(bg, key=lambda s: -s.get("startedAt", 0))]


def gather(hosts: list) -> list:
    with ThreadPoolExecutor() as pool:
        return [s for h, data in zip(hosts, pool.map(fetch, hosts)) for s in to_sessions(h, data)]


def matches(s: Session, query: str) -> bool:
    text = f"{s.host} {s.state} {s.name} {s.cwd}".lower()
    return all(word in text for word in query.lower().split())


def attach(host: str, cmd: str) -> None:
    """Run cmd on host in this terminal; reconnect while ssh reports a lost connection (exit 255)."""
    try:
        while subprocess.run(["ssh", "-t", host, remote(cmd)]).returncode == 255:
            print("connection lost, reconnecting... (Ctrl+C to give up)", file=sys.stderr)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
