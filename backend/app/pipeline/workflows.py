"""Build an n8n-style workflow graph for each trigger, with real branches.

We walk the trigger function's control-flow tree (see flow.py) and emit:

* step nodes      for functions that talk to a service (database, Slack, ...)
* agent + tools   for functions that call an LLM and other repo functions
* decision nodes  for if / switch, with yes / no (or case) edges
* error edges     for try / except handlers that actually do something
* loop nodes      for for / while / forEach, with a back-edge from the body
* end nodes       for raise / throw and early returns
* fan-out         for asyncio.gather / Promise.all

Helper functions that don't touch a service are *transparent*: their own
branches and calls are expanded in place, so detail is kept instead of being
collapsed away. Branches that lead nowhere interesting are pruned, and a node
budget keeps big codebases readable.
"""

import re
from dataclasses import dataclass, field

from ..schema import Edge, Graph, Node
from .codeindex import CodeIndex, FnRef
from .flow import Flow, iter_calls
from .integrations import Integration
from .triggers import Trigger

MAX_DEPTH = 6        # nested function expansions
STRUCTURE_DEPTH = 2  # draw branches/loops/exits only in the trigger and the functions it calls;
                     # deeper code keeps its steps but not its branching (keeps graphs readable)
MAX_NODES = 60       # per workflow (the simple view hides decisions, so allow a few more)
MAX_TOOLS = 8        # per agent
TRIGGER_ICONS = {"http": "webhook", "schedule": "schedule", "queue": "queue", "cli": "terminal",
                 "event": "event", "lambda": "cloud", "entry": "play"}
STATUS_CODE = re.compile(r"\b([1-5]\d\d)\b")
EXCEPTION_NAME = re.compile(r"^(?:raise|throw)\s+(?:new\s+)?([A-Za-z_][\w.]*)")

End = tuple[str, str, str]  # an open path: (source node id, edge kind, edge label)


def humanize(name: str) -> str:
    """'lookup_order' / 'lookupOrder' -> 'Lookup order'."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.strip("_<>")).replace("_", " ").split()
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:] if text else name


def _question(condition: str) -> str:
    """Readable fallback label for a decision, e.g. 'not order' -> 'No order?'."""
    text = condition.strip().strip("()")
    text = re.sub(r"^(not\s+|!)", "no ", text)
    for code, words in (("===", " is "), ("!==", " is not "), ("==", " is "), ("!=", " is not "),
                        ("&&", " and "), ("||", " or ")):
        text = text.replace(code, words)
    text = " ".join(text.split())
    text = text[:1].upper() + text[1:]
    return (text[:44] + "…" if len(text) > 45 else text) + "?"


def _exit_label(code: str, kind: str) -> str:
    status = STATUS_CODE.search(code)
    if kind == "raise":
        if status:
            return f"Error {status.group(1)}"
        name = EXCEPTION_NAME.match(code)
        if not name or name.group(1)[0].islower():
            return "Raise error"  # re-raising a caught variable, e.g. `throw err`
        return f"Raise {humanize(name.group(1).split('.')[-1])}"
    return f"Return {status.group(1)}" if status else "Return early"


def _handler_label(label: str) -> str:
    """'(PaymentError, TimeoutError) as e' -> 'PaymentError, TimeoutError'."""
    names = re.findall(r"[A-Z][A-Za-z0-9_]*(?:Error|Exception|Exc)\b|[A-Z][A-Za-z0-9_]+", label)
    return ", ".join(dict.fromkeys(names))[:30] if names else ""


def _dedupe(ends: list[End]) -> list[End]:
    return list(dict.fromkeys(ends))


@dataclass
class _Context:
    path: str
    depth: int                     # 0 = the trigger function itself
    stack: frozenset[tuple[str, str]]
    returns: list[End] | None      # where `return` goes inside helpers (None = trigger level)
    seen_services: set[str] = field(default_factory=set)


class WorkflowBuilder:
    def __init__(self, index: CodeIndex):
        self.index = index

    # ------------------------------------------------------------ public
    def build(self, trigger: Trigger) -> Graph:
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.warnings: list[str] = []
        fn = trigger.function

        self._add(Node(
            id="trigger", label=trigger.label, type="trigger", icon=TRIGGER_ICONS.get(trigger.kind, "play"),
            summary=fn.doc, file=trigger.path, line=fn.line, function=fn.qualname,
        ))
        ctx = _Context(trigger.path, 0, frozenset({(trigger.path, fn.qualname)}), None)
        self._sequence(self._flow_of(fn), [("trigger", "flow", "")], ctx, tail=True)
        nodes, edges = merge_repeats(self.nodes, self.edges)
        return Graph(mode="workflow", title=trigger.label, nodes=nodes, edges=edges, warnings=self.warnings)

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _flow_of(fn) -> list[Flow]:
        if fn.flow:
            return fn.flow
        return [Flow("call", callee) for callee in fn.calls]  # fallback for unparsed bodies

    def _add(self, node: Node) -> str:
        self.nodes.append(node)
        return node.id

    def _new_id(self) -> str:
        return f"n{len(self.nodes)}"

    def _connect(self, ends: list[End], target: str) -> None:
        for source, kind, label in ends:
            self.edges.append(Edge(source=source, target=target, kind=kind, label=label))

    def _checkpoint(self) -> tuple[int, int]:
        return len(self.nodes), len(self.edges)

    def _rollback(self, checkpoint: tuple[int, int]) -> None:
        self.nodes = self.nodes[:checkpoint[0]]
        self.edges = self.edges[:checkpoint[1]]

    def _budget_left(self) -> bool:
        if len(self.nodes) < MAX_NODES:
            return True
        if not self.warnings:
            self.warnings.append(f"Workflow trimmed at {MAX_NODES} nodes.")
        return False

    # ---------------------------------------------------------- sequence
    def _sequence(self, items: list[Flow], ends: list[End], ctx: _Context, tail: bool) -> list[End]:
        """Walk items in order. `tail` = nothing runs after this sequence in the workflow."""
        for i, item in enumerate(items):
            if not ends:
                break  # unreachable code after a raise / return
            has_next = i < len(items) - 1
            ends = self._item(item, ends, ctx, tail and not has_next, has_next)
        return ends

    def _item(self, item: Flow, ends: list[End], ctx: _Context, tail: bool, has_next: bool) -> list[End]:
        if ctx.depth >= STRUCTURE_DEPTH and item.kind != "call":
            # Deep inside helpers: keep the steps, skip the branching and exits.
            for arm in item.arms:
                ends = self._sequence(arm.items, ends, ctx, tail=False)
            return ends
        if item.kind == "call":
            return self._call(item, ends, ctx)
        if item.kind in {"if", "switch"}:
            return self._branch(item, ends, ctx, tail, has_next)
        if item.kind == "try":
            return self._try(item, ends, ctx, tail)
        if item.kind == "loop":
            return self._loop(item, ends, ctx)
        if item.kind == "parallel":
            return self._parallel(item, ends, ctx, tail)
        if item.kind == "return":
            return self._return(item, ends, ctx, tail)
        if item.kind == "raise":
            node_id = self._add(Node(id=self._new_id(), label=_exit_label(item.label, "raise"), type="end",
                                     icon="error", summary=item.label, file=ctx.path, line=item.line))
            self._connect(ends, node_id)
            return []
        return ends

    # -------------------------------------------------------------- calls
    def _call(self, item: Flow, ends: list[End], ctx: _Context) -> list[End]:
        if not self._budget_left():
            return ends
        target = self.index.resolve_call(ctx.path, item.label)
        if target is None:
            # A direct service call from the trigger function becomes its own step.
            service = self.index.call_integration(ctx.path, item.label) if ctx.depth == 0 else None
            if service is None or service.id in ctx.seen_services:
                return ends
            ctx.seen_services.add(service.id)
            node_id = self._add(Node(id=self._new_id(), label=f"Call {service.label}", type="integration",
                                     icon=service.category, services=[service.label], file=ctx.path, line=item.line))
            self._connect(ends, node_id)
            return [(node_id, "flow", "")]

        key = (target[0], target[1].qualname)
        if key in ctx.stack or ctx.depth >= MAX_DEPTH:
            services = self._subtree_services(target, 0, set())
            return self._step(target, services, ends) if services else ends
        return self._expand(target, ends, ctx)

    def _expand(self, ref: FnRef, ends: list[End], ctx: _Context) -> list[End]:
        path, fn = ref
        services = self.index.function_integrations(path, fn)
        uses_llm = any(s.category == "llm" for s in services)

        if uses_llm:
            tools = self._tool_refs(ref)
            if tools:
                return self._agent(ref, services, tools, ends)
            return self._step(ref, services, ends, icon="llm")

        checkpoint = self._checkpoint()
        if services:
            ends = self._step(ref, services, ends)

        returns: list[End] = []
        child = _Context(path, ctx.depth + 1, ctx.stack | {(path, fn.qualname)}, returns)
        inner = self._sequence(self._flow_of(fn), ends, child, tail=False)
        result = _dedupe(inner + returns)

        if self._checkpoint() == checkpoint:
            # The function did nothing worth drawing. Show it only when the
            # trigger calls it directly, so top-level steps never disappear.
            return self._step(ref, [], ends) if ctx.depth == 0 else ends
        return result

    def _step(self, ref: FnRef, services: list[Integration], ends: list[End], icon: str | None = None) -> list[End]:
        path, fn = ref
        primary = services[0].category if services else "code"
        node_id = self._add(Node(
            id=self._new_id(), label=humanize(fn.name), type="step", icon=icon or primary,
            summary=fn.doc, file=path, line=fn.line, function=fn.qualname, services=[s.label for s in services],
        ))
        self._connect(ends, node_id)
        return [(node_id, "flow", "")]

    def _agent(self, ref: FnRef, services: list[Integration], tools: list[FnRef], ends: list[End]) -> list[End]:
        path, fn = ref
        agent_id = self._add(Node(
            id=self._new_id(), label=humanize(fn.name), type="agent", icon="agent",
            summary=fn.doc, file=path, line=fn.line, function=fn.qualname, services=[s.label for s in services],
        ))
        self._connect(ends, agent_id)
        for tool_path, tool_fn in tools[:MAX_TOOLS]:
            tool_services = self._subtree_services((tool_path, tool_fn), 0, set())
            tool_id = self._add(Node(
                id=self._new_id(), label=humanize(tool_fn.name), type="tool",
                icon=tool_services[0].category if tool_services else "code",
                summary=tool_fn.doc, file=tool_path, line=tool_fn.line, function=tool_fn.qualname,
                services=[s.label for s in tool_services],
            ))
            self.edges.append(Edge(source=agent_id, target=tool_id, kind="tool"))
        return [(agent_id, "flow", "")]

    def _tool_refs(self, ref: FnRef) -> list[FnRef]:
        path, fn = ref
        tools: dict[tuple[str, str], FnRef] = {}
        for callee in iter_calls(self._flow_of(fn)):
            target = self.index.resolve_call(path, callee)
            if target is not None and target[1] is not fn:
                tools.setdefault((target[0], target[1].qualname), target)
        return list(tools.values())

    def _subtree_services(self, ref: FnRef, depth: int, seen: set[tuple[str, str]]) -> list[Integration]:
        path, fn = ref
        if (path, fn.qualname) in seen or depth > MAX_DEPTH:
            return []
        seen.add((path, fn.qualname))
        found = {s.id: s for s in self.index.function_integrations(path, fn)}
        for callee in fn.calls:
            target = self.index.resolve_call(path, callee)
            if target is not None:
                for service in self._subtree_services(target, depth + 1, seen):
                    found.setdefault(service.id, service)
        return list(found.values())

    # --------------------------------------------------------- structure
    def _branch(self, item: Flow, ends: list[End], ctx: _Context, tail: bool, has_next: bool) -> list[End]:
        checkpoint = self._checkpoint()
        returns_before = len(ctx.returns) if ctx.returns is not None else 0
        label = f"Which {item.label}?"[:46] if item.kind == "switch" else _question(item.label)
        decision = self._add(Node(id=self._new_id(), label=label, type="decision", icon="decision",
                                  summary=f"{item.kind} {item.label}", file=ctx.path, line=item.line))
        self._connect(ends, decision)

        has_else = bool(item.arms) and item.arms[-1].label == "else"
        binary = item.kind == "if" and len(item.arms) <= 2
        out: list[End] = []
        meaningful = False

        for index, arm in enumerate(item.arms):
            if binary:
                kind, edge_label = ("yes", "") if index == 0 else ("no", "")
            else:
                kind, edge_label = "flow", ("else" if arm.label == "else" else arm.label.replace("case ", ""))[:24]
            arm_checkpoint = self._checkpoint()
            arm_ends = self._sequence(arm.items, [(decision, kind, edge_label)], ctx, tail)
            added_nodes = self._checkpoint()[0] > arm_checkpoint[0]
            exited_early = not arm_ends and has_next  # an arm that stops the flow before later steps
            if added_nodes or exited_early:
                meaningful = True
            out.extend(arm_ends)

        if not has_else:
            out.append((decision, "no" if binary else "flow", "" if binary else "else"))

        if not meaningful:
            self._rollback(checkpoint)
            if ctx.returns is not None:
                del ctx.returns[returns_before:]
            return ends
        return _dedupe(out)

    def _try(self, item: Flow, ends: list[End], ctx: _Context, tail: bool) -> list[End]:
        body, handlers = item.arms[0], item.arms[1:]
        before = self._checkpoint()
        body_ends = self._sequence(body.items, ends, ctx, tail and not handlers)
        body_nodes = [n for n in self.nodes[before[0]:] if n.type != "end"]
        if not body_nodes:
            return body_ends
        anchor = body_nodes[-1].id  # the last real step is the one most likely to fail

        out = list(body_ends)
        for handler in handlers:
            checkpoint = self._checkpoint()
            start: list[End] = [(anchor, "error", _handler_label(handler.label))]
            handler_ends = self._sequence(handler.items, start, ctx, tail)
            if self._checkpoint()[0] == checkpoint[0]:
                self._rollback(checkpoint)  # handler only logs or swallows: not worth drawing
                continue
            out.extend(handler_ends)
        return _dedupe(out)

    def _loop(self, item: Flow, ends: list[End], ctx: _Context) -> list[End]:
        if not item.arms:
            return ends
        checkpoint = self._checkpoint()
        target = item.label
        if target.startswith("while"):
            label = humanize(target)
        else:
            label = f"For each {re.split(r' in | of ', target)[0]}"
        loop_id = self._add(Node(id=self._new_id(), label=label[:46], type="loop", icon="loop",
                                 summary=f"for {item.label}", file=ctx.path, line=item.line))
        self._connect(ends, loop_id)
        body_ends = self._sequence(item.arms[0].items, [(loop_id, "flow", "each")], ctx, tail=False)
        if self._checkpoint()[0] == checkpoint[0] + 1:
            self._rollback(checkpoint)  # nothing interesting happens per item
            return ends
        for source, _, _ in body_ends:
            if source != loop_id:
                self.edges.append(Edge(source=source, target=loop_id, kind="loop"))
        return [(loop_id, "flow", "done")]

    def _parallel(self, item: Flow, ends: list[End], ctx: _Context, tail: bool) -> list[End]:
        out: list[End] = []
        for arm in item.arms:
            out.extend(self._sequence(arm.items, ends, ctx, tail))
        return _dedupe(out) or ends

    def _return(self, item: Flow, ends: list[End], ctx: _Context, tail: bool) -> list[End]:
        if ctx.returns is not None:
            ctx.returns.extend(ends)  # inside a helper: control goes back to the caller
            return []
        if tail:
            return ends  # the normal end of the workflow; no node needed
        node_id = self._add(Node(id=self._new_id(), label=_exit_label(item.label, "return"), type="end",
                                 icon="stop", summary=item.label, file=ctx.path, line=item.line))
        self._connect(ends, node_id)
        return []


MERGEABLE = {"step", "integration"}


def merge_repeats(nodes: list[Node], edges: list[Edge]) -> tuple[list[Node], list[Edge]]:
    """Collapse a straight run of the same step into one node: "Database session (x4)".

    Code often opens a DB session or calls the same helper several times in a
    row; drawing each call adds boxes but no meaning. Two nodes merge when they
    call the same function (or have the same label and services) and the first
    flows only into the second, which has no other way in.
    """
    by_id = {n.id: n for n in nodes}
    counts = {n.id: 1 for n in nodes}
    changed = True
    while changed:
        changed = False
        outgoing: dict[str, list[Edge]] = {}
        incoming: dict[str, list[Edge]] = {}
        for edge in edges:
            outgoing.setdefault(edge.source, []).append(edge)
            incoming.setdefault(edge.target, []).append(edge)
        for edge in edges:
            first, second = by_id.get(edge.source), by_id.get(edge.target)
            if (edge.kind != "flow" or first is None or second is None or first.id == second.id
                    or first.type not in MERGEABLE or second.type not in MERGEABLE
                    or len(outgoing[first.id]) != 1 or len(incoming[second.id]) != 1
                    or not _same_step(first, second)):
                continue
            # Fold `second` into `first`: its outgoing edges now leave from `first`.
            counts[first.id] += counts.pop(second.id)
            del by_id[second.id]
            edges = [e for e in edges if e is not edge]
            edges = [e.model_copy(update={"source": first.id}) if e.source == second.id else e for e in edges]
            changed = True
            break
    kept = [n for n in nodes if n.id in by_id]
    for node in kept:
        if counts[node.id] > 1:
            node.label = f"{node.label} (x{counts[node.id]})"
            node.summary = (node.summary + " " if node.summary else "") + f"Called {counts[node.id]} times in a row."
    return kept, edges


def _same_step(a: Node, b: Node) -> bool:
    if a.function and b.function:
        return a.function == b.function and a.file == b.file
    return a.label == b.label and a.services == b.services
