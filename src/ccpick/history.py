"""Runs on the remote host (`python3 -`): print past conversations as a JSON array.

Reads Claude Code transcripts (<config>/projects/<project>/<session-id>.jsonl) from the default
config dir and from each cswap account profile. Transcripts are not a stable interface, so this
reads only titles, cwd and mtime from the last 64 KB of each file and skips anything it can't parse.
"""

import glob
import json
import os

TAIL = 65536


def tail_lines(path):
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - TAIL))
        lines = f.read().decode("utf-8", "replace").splitlines()
    return lines[1:] if size > TAIL else lines  # the first line of a partial read is cut off


def info(path):
    """Title and cwd of one transcript, or None when it was continued in another session."""
    found = {}
    for line in reversed(tail_lines(path)):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "continued-in":
            return None
        for key in ("customTitle", "aiTitle", "lastPrompt", "cwd"):
            if isinstance(o.get(key), str) and key not in found:
                found[key] = o[key]
    title = found.get("customTitle") or found.get("aiTitle") or found.get("lastPrompt", "")[:80]
    return {"title": title or "(untitled)", "cwd": found.get("cwd", "")}


def roots():
    """(account, config dir): "default" for the default login, else the cswap account number."""
    home = os.path.expanduser("~")
    yield "default", os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    for base in (os.path.join(data, "claude-swap"), os.path.join(home, ".claude-swap-backup")):
        for d in sorted(glob.glob(os.path.join(base, "sessions", "*-*"))):
            yield os.path.basename(d).split("-", 1)[0], d


def main():
    out, seen = [], set()
    for account, root in roots():
        for path in glob.glob(os.path.join(root, "projects", "*", "*.jsonl")):
            real = os.path.realpath(path)
            if real in seen:  # a profile that shares history links to the default projects dir
                continue
            seen.add(real)
            try:
                i = info(path)
            except OSError:
                continue
            if i:
                out.append(dict(i, account=account, sessionId=os.path.basename(path)[:-6],
                                mtime=int(os.path.getmtime(path) * 1000)))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
