"""forge tui — a Textual cockpit around the same command set as `forge shell`.

Layout: services sidebar | scrolling command log | input line with TAB
completion popup. Commands run in worker threads (schema extract takes
minutes); their stdout/stderr is redirected into the log, and a failing
command never takes the app down.
"""
from __future__ import annotations

import contextlib
import io
import pathlib

from textual.binding import Binding
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from .shell import ForgeCompleter

PROMPT = "forge> "


class _StreamToLog(io.TextIOBase):
    """stdout/stderr replacement that funnels complete lines into the log.
    print() writes in fragments (arg, sep, arg, end) — buffer until newline."""

    def __init__(self, emit):
        self.emit = emit
        self.buf = ""

    def write(self, s) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.emit(line)
        return len(s)

    def flush(self) -> None:
        if self.buf.strip():
            self.emit(self.buf.strip())
        self.buf = ""


class ForgeTUI(App):
    TITLE = "brokerpak-forge"
    CSS = """
    #body { height: 1fr; }
    #sidebar { width: 24; border-right: solid $primary; padding: 0 1; }
    #sidebar Label { color: $text-muted; }
    #sidebar ListView { height: auto; }
    #log { height: 1fr; }
    #completion { height: auto; max-height: 10; display: none;
                  border: round $accent; background: $surface; }
    #statusbar { dock: bottom; height: 1; background: $primary; color: $text; }
    Input { dock: bottom; }
    """

    BINDINGS = [Binding("tab", "complete", "Complete", priority=True)]

    def __init__(self, cli_main):
        super().__init__()
        self.cli_main = cli_main
        self.completer = ForgeCompleter()
        self.history: list[str] = []
        self.hist_idx: int | None = None
        self._just_completed = False
        self.emitted: list[str] = []   # test hook: everything the log received

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("services")
                yield ListView(id="services")
                yield Label("TAB completes")
                yield Label("click a service → show")
            with Vertical():
                yield RichLog(id="log", highlight=True, markup=False, max_lines=2000)
                yield ListView(id="completion")
        yield Input(placeholder="command… (help, exit)")
        yield Static("", id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self.log_line("forge tui — the same commands as `forge shell`. exit to leave.")
        sidebar = self.query_one("#services", ListView)
        for name in self._service_names():
            item = ListItem(Label(name))
            item.service = name  # type: ignore[attr-defined]
            sidebar.append(item)
        self._set_status()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _service_names() -> list[str]:
        d = pathlib.Path("services")
        return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []

    def log_line(self, line: str) -> None:
        self.emitted.append(line)
        self.query_one("#log", RichLog).write(line)

    def _set_status(self) -> None:
        schema = pathlib.Path("schema").exists() and any(pathlib.Path("schema").glob("*.json"))
        manifest = pathlib.Path("manifest.yml").exists()
        self.query_one("#statusbar", Static).update(
            f" services: {len(self._service_names())}   manifest: {'yes' if manifest else 'no'}"
            f"   schema: {'yes' if schema else 'no'}")

    # --------------------------------------------------------- completion

    def _current_candidates(self) -> list[tuple[str, str]]:
        value = self.query_one(Input).value
        parts = value.split()
        if not value or value.endswith(" "):
            done, word = value, ""
        elif parts:
            done, word = " ".join(parts[:-1]), parts[-1]
        else:
            done, word = "", ""
        return [(c, m) for c, m in self.completer._candidates(done)
                if c.startswith(word)][:8]

    def _refresh_completion(self) -> None:
        box = self.query_one("#completion", ListView)
        box.clear()
        cands = self._current_candidates()
        for cand, meta in cands:
            item = ListItem(Label(f"{cand}  — {meta}"))
            item.candidate = cand  # type: ignore[attr-defined]
            box.append(item)
        box.display = bool(cands)
        if cands:
            box.index = 0

    def action_complete(self) -> None:
        box = self.query_one("#completion", ListView)
        if not box.display or box.index is None:
            return
        items = [i for i in box.children if isinstance(i, ListItem)]
        cand = getattr(items[box.index], "candidate", None)
        if cand is None:
            return
        inp = self.query_one(Input)
        parts = inp.value.split()
        if parts and cand.startswith(parts[-1]):
            parts[-1] = cand
        else:
            parts.append(cand)
        inp.value = " ".join(parts)
        inp.cursor_position = len(inp.value)
        self._just_completed = True
        box.display = False

    # ---------------------------------------------------------- execution

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._just_completed:
            self._just_completed = False
            return
        self._refresh_completion()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        cmd = event.value.strip()
        inp = self.query_one(Input)
        inp.value = ""
        self.query_one("#completion", ListView).display = False
        if not cmd:
            return
        self.log_line(PROMPT + cmd)
        if cmd in ("exit", "quit"):
            self.exit()
            return
        if cmd == "help":
            from .shell import HELP
            for line in HELP.splitlines():
                self.log_line(line)
            return
        if self.history and self.history[-1] != cmd:
            self.history.append(cmd)
        elif not self.history:
            self.history.append(cmd)
        self.hist_idx = None
        self.run_worker(lambda: self._exec(cmd), thread=True, exclusive=True)

    def _exec(self, cmd: str) -> None:
        stream = _StreamToLog(lambda line: self.call_from_thread(self.log_line, line))
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            try:
                self.cli_main(cmd.split())
            except SystemExit as e:
                if e.args and isinstance(e.args[0], str) and e.args[0]:
                    self.call_from_thread(self.log_line, e.args[0])
            except Exception as e:  # a failing command must not kill the app
                if str(e):
                    self.call_from_thread(self.log_line, f"error: {e}")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if getattr(event.item, "service", None):
            self.query_one(Input).value = f"{event.item.service} show"
            self.query_one(Input).action_submit()

    # key handling: history via up/down, tab completes
    def on_key(self, event) -> None:
        inp = self.query_one(Input)
        if not inp.has_focus:
            return
        if event.key == "up" and not self.history:
            return
        if event.key == "up":
            self.hist_idx = len(self.history) - 1 if self.hist_idx is None else max(0, self.hist_idx - 1)
            inp.value = self.history[self.hist_idx]
            inp.cursor_position = len(inp.value)
            event.prevent_default()
        elif event.key == "down" and self.hist_idx is not None:
            self.hist_idx += 1
            if self.hist_idx >= len(self.history):
                self.hist_idx = None
                inp.value = ""
            else:
                inp.value = self.history[self.hist_idx]
                inp.cursor_position = len(inp.value)
            event.prevent_default()

