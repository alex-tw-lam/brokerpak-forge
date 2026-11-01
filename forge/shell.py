"""forge shell — a REPL built on prompt_toolkit (the library behind Azure CLI's
interactive mode): drop-down TAB completion, persistent history, and errors that
never kill the session."""
import pathlib
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

TOP = ["manifest", "schema", "service", "validate", "check", "shell", "help", "exit", "quit"]
SCHEMA_SUB = ["extract", "list", "show"]
SERVICE_SUB = ["new", "move", "show", "gen", "import", "scaffold"]
ACTIONS = ["show", "move", "gen", "scaffold"]
MOVE_TARGETS = ["user", "plan", "computed", "skipped"]

HISTORY_FILE = pathlib.Path.home() / ".forge_history"


def _service_names() -> list[str]:
    d = pathlib.Path("services")
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []


def _fields(service: str) -> list[str]:
    try:
        from .spec import load
        spec = load(service)
        return (list(spec.inputs) + [c.name for c in spec.bind.computed]
                + spec.skipped_inputs)
    except SystemExit:
        return []


def _resources() -> list[str]:
    try:
        from .schema import Schema
        return Schema().resources(None)
    except SystemExit:
        return []


class ForgeCompleter(Completer):
    def __init__(self):
        self.resources: list[str] | None = None

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        for cand, meta in self._candidates(text):
            if cand.startswith(word or ""):
                yield Completion(cand, start_position=-len(word), display_meta=meta)

    def _candidates(self, text: str) -> list[tuple[str, str]]:
        done = text.split()
        services = _service_names()

        if not done:
            return [(c, "command") for c in TOP] + [(s, "service") for s in services]

        head = done[0]
        if head in services:
            if len(done) == 1:
                return [(a, "action") for a in ACTIONS]
            if done[1] == "move":
                if len(done) == 2:
                    return [(f, "parameter") for f in _fields(head)]
                return [(t, "treatment") for t in MOVE_TARGETS] + \
                       [("--to", "option"), ("--default", "option")]
            return []
        if head in ("exit", "quit", "help", "check", "validate", "shell"):
            return []
        if head == "manifest":
            return [(f, "option") for f in ("--bin-dir", "--name", "--org", "--author",
                                            "--description", "--write")]
        if head == "schema":
            if len(done) == 1:
                return [(s, "subcommand") for s in SCHEMA_SUB]
            sub = done[1]
            if sub == "list":
                return [("-d", "data sources")]
            if sub == "show":
                if self.resources is None:
                    self.resources = _resources()
                return [(r, "resource") for r in self.resources]
            return []
        if head == "service":
            if len(done) == 1:
                return [(s, "subcommand") for s in SERVICE_SUB]
            sub = done[1]
            if sub in ("show", "gen", "move") and len(done) == 2:
                return [(s, "service") for s in services]
            if sub == "move" and len(done) == 3:
                return [(f, "parameter") for f in _fields(done[2])]
            if sub in ("move", "new", "import") and len(done) >= 2:
                opts = [("--to", "option"), ("--default", "option"),
                        ("--resource", "option"), ("--yes", "option"), ("--name", "option")]
                return opts + [(t, "treatment") for t in MOVE_TARGETS]
            return []
        return []


HELP = """\
commands: manifest, schema, service, validate, check, shell
  schema extract | list [pattern] [-d] | show NAME [-d]
  service new NAME [--resource R] [--yes] | move SVC FIELD --to user|plan|computed|skipped
  service show SVC | gen SVC --write|--check | import FILE --name N
  <service> show | move FIELD --to ... | gen | scaffold
"""

def run(cli_main) -> None:
    session: PromptSession = PromptSession(
        "forge> ",
        completer=ForgeCompleter(),
        history=FileHistory(str(HISTORY_FILE)),
        complete_while_typing=False,
    )
    print("forge shell — TAB completes commands, services, parameters and resources. "
          "Errors do not exit; `exit` does.")
    while True:
        try:
            line = session.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            print(HELP)
            continue
        try:
            cli_main(line.split())
        except SystemExit as e:
            # sys.exit("message") carries the message; plain exit codes stay quiet
            if e.args and isinstance(e.args[0], str) and e.args[0]:
                print(e.args[0], file=sys.stderr)
        except KeyboardInterrupt:
            print("^C")
        except Exception as e:  # a failing command must not kill the session
            if str(e):
                print(f"error: {e}", file=sys.stderr)
