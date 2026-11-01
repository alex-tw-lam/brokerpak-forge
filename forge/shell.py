"""forge shell — a REPL with TAB completion, context-aware per command."""
import pathlib
import readline
import sys

TOP = ["manifest", "schema", "service", "help", "exit", "quit"]
SCHEMA_SUB = ["extract", "list", "show"]
SERVICE_SUB = ["new", "move", "show", "gen"]
MOVE_TARGETS = ["user", "plan", "computed", "skipped"]


def _service_names() -> list[str]:
    d = pathlib.Path("services")
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []


def _fields(service: str) -> list[str]:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from forge.spec import load
        spec = load(service)
        names = list(spec.inputs)
        names += [c.name for c in spec.bind.computed]
        names += spec.skipped_inputs
        return names
    except SystemExit:
        return []


def _resources() -> list[str]:
    try:
        from forge.schema import Schema
        return Schema().resources(None)
    except SystemExit:
        return []


class Completer:
    def __init__(self):
        self.resources: list[str] | None = None

    def complete(self, text: str, state: int):
        line = readline.get_line_buffer()
        cands = self._candidates(line, text)
        matches = [c for c in cands if c.startswith(text)]
        if state < len(matches):
            suffix = " " if not matches[state].endswith(("=", "-")) else ""
            return matches[state] + suffix
        return None

    def _candidates(self, line: str, text: str) -> list[str]:
        parts = line.lstrip().split()
        # completing a fresh word: use tokens without the in-progress one
        done = parts[:-1] if text else parts

        if not done:
            return TOP
        head = done[0]

        if head in ("exit", "quit", "help"):
            return []
        if head == "manifest":
            return ["--bin-dir", "--name", "--org", "--author", "--description", "--write"]
        if head == "schema":
            if len(done) == 1:
                return SCHEMA_SUB
            sub = done[1]
            if sub == "list":
                return ["-d"] if not [t for t in done[2:] if not t.startswith("-")] else []
            if sub == "show":
                if self.resources is None:
                    self.resources = _resources()
                return self.resources
            return []
        if head == "service":
            if len(done) == 1:
                return SERVICE_SUB
            sub = done[1]
            if sub in ("show", "gen") and len(done) == 2:
                return _service_names()
            if sub == "new" and len(done) == 2:
                return ["--resource", "--yes"]
            if sub == "move":
                if len(done) == 2:
                    return _service_names()
                if len(done) == 3:
                    return _fields(done[2])
                return ["--to", "--default"] + MOVE_TARGETS
            return []
        return []


HISTORY_FILE = pathlib.Path.home() / ".forge_history"


def run(cli_main) -> None:
    readline.set_completer(Completer().complete)
    readline.parse_and_bind("tab: complete")
    if HISTORY_FILE.exists():
        readline.read_history_file(HISTORY_FILE)

    print("forge shell — TAB completes commands, services, fields and resources. exit to leave.")
    while True:
        try:
            line = input("forge> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            print("commands:", ", ".join(TOP[:-2]))
            print("  schema extract | list [pattern] [-d] | show NAME [-d]")
            print("  service new NAME [--resource R] [--yes] | move SVC FIELD --to user|plan|computed|skipped")
            print("  service show SVC | gen SVC --write|--check")
            continue
        try:
            cli_main(line.split())
        except SystemExit:
            pass
    readline.set_history_length(500)
    readline.write_history_file(HISTORY_FILE)
