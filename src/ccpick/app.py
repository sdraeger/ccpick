"""Browse and attach to Claude Code background sessions on your SSH hosts."""

import argparse
import os
import shlex
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input

from ccpick import __version__
from ccpick.sessions import attach, gather, matches

STATE_STYLE = {"working": "green", "blocked": "bold yellow", "done": "dim", "failed": "red",
               "stopped": "dim red", "unreachable": "red"}


def age(ms: int) -> str:
    if not ms:
        return ""
    s = time.time() - ms / 1000
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{unit}"
    return "now"


class Picker(App):
    TITLE = "ccpick"
    BINDINGS = [
        Binding("enter", "attach", "Attach", priority=True),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
        Binding("ctrl+n", "agents", "Agent view", priority=True),
        Binding("ctrl+r", "reload", "Refresh"),
        Binding("escape", "quit", "Quit"),
    ]

    def __init__(self, hosts: list):
        super().__init__()
        self.hosts = hosts
        self.sessions: list = []
        self.shown: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="filter by host, state, name or directory")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Host", "State", "Name", "Directory", "Age")
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        self.call_from_thread(setattr, self, "sub_title", "loading...")
        sessions = gather(self.hosts)
        self.call_from_thread(self.show, sessions)

    def show(self, sessions: list) -> None:
        self.sessions = sessions
        n = sum(1 for s in sessions if s.id)
        self.sub_title = f"{n} sessions on {len(self.hosts)} host{'s' * (len(self.hosts) > 1)}"
        self.refilter()

    def refilter(self) -> None:
        table = self.query_one(DataTable)
        query = self.query_one(Input).value
        self.shown = [s for s in self.sessions if matches(s, query)]
        table.clear()
        for s in self.shown:
            table.add_row(s.host, Text(s.state, style=STATE_STYLE.get(s.state, "")), s.name, s.cwd,
                          age(s.started))

    def on_input_changed(self) -> None:
        self.refilter()

    def selected(self):
        table = self.query_one(DataTable)
        return self.shown[table.cursor_row] if self.shown else None

    def action_move(self, step: int) -> None:
        table = self.query_one(DataTable)
        table.action_cursor_down() if step > 0 else table.action_cursor_up()

    def action_reload(self) -> None:
        self.load()

    def action_attach(self) -> None:
        s = self.selected()
        if s is None or not s.id:
            self.bell()
            return
        self.run_remote(s.host, f"claude attach {shlex.quote(s.id)}")

    def action_agents(self) -> None:
        s = self.selected()
        self.run_remote(s.host if s else self.hosts[0], "claude agents")

    def run_remote(self, host: str, cmd: str) -> None:
        with self.suspend():
            attach(host, cmd)
        self.load()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ccpick", description=__doc__)
    p.add_argument("hosts", nargs="*", help="SSH hosts to list (default: $CCPICK_HOSTS)")
    p.add_argument("-l", "--list", action="store_true",
                   help="print sessions as tab-separated rows (host, id, state, name, cwd) and exit")
    p.add_argument("-V", "--version", action="version", version=f"ccpick {__version__}")
    a = p.parse_args(argv)
    hosts = a.hosts or os.environ.get("CCPICK_HOSTS", "").split()
    if not hosts:
        p.error("no hosts given: pass them as arguments or set CCPICK_HOSTS")
    if a.list:
        for s in gather(hosts):
            print("\t".join([s.host, s.id, s.state, s.name, s.cwd]))
        return 0
    Picker(hosts).run()
    return 0
