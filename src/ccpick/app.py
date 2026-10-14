"""Browse and attach to Claude Code background sessions on your SSH hosts."""

import argparse
import os
import shlex
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, OptionList

from ccpick import __version__
from ccpick.sessions import attach, claude_cmd, gather, matches, switch

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


class Filter(Input):
    BINDINGS = [Binding("enter", "app.attach", "Attach"), Binding("up", "app.move(-1)", show=False),
                Binding("down", "app.move(1)", show=False)]


class AccountPicker(ModalScreen):
    """Choose a cswap account; dismisses with the Account or None."""

    DEFAULT_CSS = """
    AccountPicker { align: center middle; }
    AccountPicker OptionList { width: auto; min-width: 50; height: auto; border: round $accent; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, title: str, accounts: list, highlight: int = 0):
        super().__init__()
        self.title, self.accounts, self.highlight = title, accounts, highlight

    def compose(self) -> ComposeResult:
        w = max(len(a.label) for a in self.accounts)
        options = OptionList(*[f"{a.label:<{w}}   {a.usage}{'   (default)' * a.active}" for a in self.accounts])
        options.border_title = self.title
        yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = self.highlight

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.accounts[event.option_index])


class Picker(App):
    TITLE = "ccpick"
    BINDINGS = [
        Binding("ctrl+n", "new", "New session", priority=True),
        Binding("ctrl+s", "swap", "Swap account", priority=True),
        Binding("ctrl+r", "reload", "Refresh"),
        Binding("escape", "quit", "Quit"),
    ]

    def __init__(self, hosts: list):
        super().__init__()
        self.hosts = hosts
        self.sessions: list = []
        self.accounts: dict = {}
        self.shown: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Filter(placeholder="filter by host, account, state, name or directory")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Host", "Account", "State", "Name", "Directory", "Age")
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        self.call_from_thread(setattr, self, "sub_title", "loading...")
        self.call_from_thread(self.show, *gather(self.hosts))

    def show(self, sessions: list, accounts: dict) -> None:
        self.sessions, self.accounts = sessions, accounts
        n = sum(1 for s in sessions if s.id)
        self.sub_title = f"{n} sessions on {len(self.hosts)} host{'s' * (len(self.hosts) > 1)}"
        self.refilter()

    def refilter(self) -> None:
        table = self.query_one(DataTable)
        query = self.query_one(Filter).value
        self.shown = [s for s in self.sessions if matches(s, query)]
        table.clear()
        for s in self.shown:
            table.add_row(s.host, s.account.label, Text(s.state, style=STATE_STYLE.get(s.state, "")),
                          s.name, s.cwd, age(s.started))

    def on_input_changed(self) -> None:
        self.refilter()

    def on_data_table_row_selected(self) -> None:
        self.action_attach()

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
        self.run_remote(s.host, claude_cmd(s.account, f"attach {shlex.quote(s.id)}"))

    def action_new(self) -> None:
        """Open agent view on the selected host, asking for the account when there are several."""
        if isinstance(self.screen, AccountPicker):
            return
        host = self.current_host()
        accounts = self.accounts.get(host)
        if not accounts:
            self.bell()
            return
        if len(accounts) == 1:
            self.run_remote(host, claude_cmd(accounts[0], "agents"))
            return
        self.push_screen(AccountPicker(f"New session on {host} as", accounts),
                         lambda a: a and self.run_remote(host, claude_cmd(a, "agents")))

    def action_swap(self) -> None:
        """Set the host's default cswap account; the first non-default one is preselected."""
        if isinstance(self.screen, AccountPicker):
            return
        host = self.current_host()
        accounts = [a for a in self.accounts.get(host) or [] if a.slot]
        if len(accounts) < 2:
            self.notify(f"{host} has no other cswap account to swap to", severity="warning")
            return
        other = next((i for i, a in enumerate(accounts) if not a.active), 0)
        self.push_screen(AccountPicker(f"Default account on {host}", accounts, other),
                         lambda a: a and not a.active and self.swap(host, a))

    @work(thread=True, group="swap")
    def swap(self, host: str, account) -> None:
        ok, message = switch(host, account)
        if ok:
            self.call_from_thread(self.notify, f"{host}: default account is now {account.label}")
            self.call_from_thread(self.load)
        else:
            self.call_from_thread(self.notify, f"{host}: cswap switch failed: {message}", severity="error")

    def current_host(self) -> str:
        s = self.selected()
        return s.host if s else self.hosts[0]

    def run_remote(self, host: str, cmd: str) -> None:
        with self.suspend():
            attach(host, cmd)
        self.load()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ccpick", description=__doc__)
    p.add_argument("hosts", nargs="*", help="SSH hosts to list (default: $CCPICK_HOSTS)")
    p.add_argument("-l", "--list", action="store_true",
                   help="print sessions as tab-separated rows (host, account, id, state, name, cwd)")
    p.add_argument("-V", "--version", action="version", version=f"ccpick {__version__}")
    a = p.parse_args(argv)
    hosts = a.hosts or os.environ.get("CCPICK_HOSTS", "").split()
    if not hosts:
        p.error("no hosts given: pass them as arguments or set CCPICK_HOSTS")
    if a.list:
        for s in gather(hosts)[0]:
            print("\t".join([s.host, s.account.label, s.id, s.state, s.name, s.cwd]))
        return 0
    Picker(hosts).run()
    return 0
