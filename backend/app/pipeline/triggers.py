"""Find workflow entry points: HTTP routes, schedules, queue workers, CLIs, events.

Detection is rule-based (decorators, call patterns, well-known names), which is
fast, deterministic and needs no model.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .parser import FunctionInfo, ParsedFile

PY_ROUTE = re.compile(
    r"^(?P<obj>[\w.]+)\.(?P<method>get|post|put|patch|delete|head|options|route|api_route|websocket)"
    r"\s*\(\s*[rf]?(?P<q>['\"])(?P<path>[^'\"]*)(?P=q)(?P<rest>.*)",
    re.S,
)
NEST_ROUTE = re.compile(r"^(?P<method>Get|Post|Put|Patch|Delete|All)\s*\(\s*(?:['\"`](?P<path>[^'\"`]*))?")
METHODS_ARG = re.compile(r"methods\s*=\s*\[\s*['\"](\w+)")

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("schedule", re.compile(r"scheduled_job|repeat_every|periodic_task|crontab|(^|\.)cron\b|^Cron\(|^Interval\(", re.I)),
    ("queue", re.compile(r"(^|\.)task\b|shared_task|(^|\.)actor\b|^Processor\(|^Process\(|MessagePattern|EventPattern|SqsMessageHandler")),
    ("cli", re.compile(r"(^|\.)command\b|click\.group|(^|\.)callback\b|^Command\(")),
    ("event", re.compile(r"(^|\.)event\b|(^|\.)on_event\b|(^|\.)message\b|(^|\.)listener\b|(^|\.)on\(|^OnEvent\(|subscribe")),
]
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$")
ENTRY_FILES = {"main.py", "app.py", "cli.py", "run.py", "bot.py", "worker.py",
               "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts", "app.js", "app.ts"}


@dataclass(frozen=True) 
class Trigger:
    kind: str      # http | schedule | queue | cli | event | lambda | entry
    label: str     # e.g. "POST /refunds"
    path: str      # file
    function: FunctionInfo


def _from_decorators(fn: FunctionInfo, prefixes: dict[str, str]) -> tuple[str, str] | None:
    for deco in fn.decorators:
        if m := PY_ROUTE.match(deco):
            method = m["method"].upper()
            if method in {"ROUTE", "API_ROUTE"}:
                found = METHODS_ARG.search(m["rest"])
                method = found.group(1).upper() if found else "GET"
            elif method == "WEBSOCKET":
                method = "WS"
            # router = APIRouter(prefix="/training") + @router.post("/rgb") -> POST /training/rgb
            prefix = prefixes.get(m["obj"], "").rstrip("/")
            return "http", f"{method} {prefix + (m['path'] or '') or '/'}"
        if m := NEST_ROUTE.match(deco):
            return "http", f"{m['method'].upper()} /{(m['path'] or '').lstrip('/')}"
        for kind, pattern in RULES:
            if pattern.search(deco):
                return kind, f"{kind.title()}: {fn.name}"
    return None


def _from_hint(fn: FunctionInfo) -> tuple[str, str] | None:
    if not fn.trigger_hint:
        return None
    kind, _, rest = fn.trigger_hint.partition(":")
    if kind == "http":
        method, _, route = rest.partition(":")
        return "http", f"{method} {route or '/'}"
    if kind == "cli":
        return "cli", "Run as script"
    return kind, f"{kind.title()}: {rest or fn.name}"


def _is_lambda(fn: FunctionInfo) -> bool:
    return fn.name in {"handler", "lambda_handler"} and fn.params.replace(" ", "").startswith(("event", "event,"))


def find_triggers(files: list[ParsedFile]) -> list[Trigger]:
    triggers: list[Trigger] = []
    files = [f for f in files if not TEST_FILE.search(f.path)]  # tests are not workflows
    for f in files:
        for fn in f.functions:
            found = _from_hint(fn) or _from_decorators(fn, f.route_prefixes)
            if found is None and _is_lambda(fn):
                found = ("lambda", f"Lambda: {fn.name}")
            if found is not None:
                kind, label = found
                if fn.trigger_hint == "cli:main":
                    label = f"Run {PurePosixPath(f.path).name}"
                triggers.append(Trigger(kind, label, f.path, fn))

    if not triggers:
        triggers = _fallback_entries(files)
    triggers.sort(key=lambda t: (t.kind != "http", t.path, t.function.line))
    return triggers


def _fallback_entries(files: list[ParsedFile]) -> list[Trigger]:
    """No framework patterns found: use entry files or `main()` functions."""
    entries: list[Trigger] = []
    for f in files:
        name = PurePosixPath(f.path).name
        for fn in f.functions:
            is_entry_module = fn.name == "<module>" and name in ENTRY_FILES and fn.calls
            if is_entry_module or (fn.name == "main" and not fn.synthetic):
                entries.append(Trigger("entry", f"Run {name}", f.path, fn))
    return entries
