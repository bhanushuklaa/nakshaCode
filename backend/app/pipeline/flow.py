"""Control-flow trees: what a function does, in order, with its branches."""

from dataclasses import dataclass, field

from tree_sitter import Node as TSNode

MAX_DEPTH = 12          # nested blocks before we fall back to flat calls
MAX_ITEMS = 300         # items per function, to bound memory on huge functions

PARALLEL_CALLEES = {"asyncio.gather", "gather", "asyncio.wait", "Promise.all", "Promise.allSettled", "Promise.race"}
JS_LOOP_METHODS = (".forEach", ".map", ".flatMap", ".filter", ".reduce", ".some", ".every")
JS_FUNCTION_NODES = {"arrow_function", "function_expression", "function", "generator_function"}
NESTED_DEFINITIONS = {
    "function_definition", "class_definition", "decorated_definition",               # Python
    "function_declaration", "generator_function_declaration", "class_declaration",   # JS / TS
    "method_definition", "interface_declaration", "type_alias_declaration",
}
CALL_NODES = {"call", "call_expression", "new_expression"}


@dataclass
class Arm:
    """One path of a branch: an `if` arm, an `except` handler, a loop body..."""
    label: str
    items: list["Flow"]


@dataclass
class Flow:
    """One step of a function's control flow.

    kind:  call | if | switch | try | loop | parallel | return | raise
    label: callee for calls, condition for branches, code text for exits
    arms:  sub-paths for if / switch / try / loop / parallel
           (for `try`, arms[0] is the protected body and the rest are handlers)
    """
    kind: str
    label: str = ""
    line: int = 0
    arms: list[Arm] = field(default_factory=list)


def _text(node: TSNode | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _short(text: str, limit: int = 80) -> str:
    return " ".join(text.split())[:limit]


def _callee(node: TSNode) -> str:
    target = node.child_by_field_name("consFlowLenstructor") if node.type == "new_expression" else node.child_by_field_name("function")
    return _short(_text(target))


# --------------------------------------------------------------------------- #
# Calls inside an expression (shared by both languages)
# --------------------------------------------------------------------------- #

def calls_in(node: TSNode | None, language: str) -> list[Flow]:
    """Calls inside an expression, in *execution* order.

    Inner calls run before outer ones (`save(transform(x))` runs transform
    first), so we sort by where each call *ends*. Parallel helpers
    (`asyncio.gather`, `Promise.all`) and JS array loops (`items.forEach(fn)`)
    become `parallel` / `loop` items instead of plain calls.
    """
    if node is None:
        return []
    found: list[tuple[int, Flow]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        kind = current.type
        if kind in NESTED_DEFINITIONS:
            continue
        if kind == "variable_declarator":
            value = current.child_by_field_name("value")
            if value is not None and value.type in JS_FUNCTION_NODES:
                continue  # `const f = () => ...` defines a function; it does not run it

        if kind in CALL_NODES:
            callee = _callee(current)
            args = current.child_by_field_name("arguments")
            line = current.start_point[0] + 1

            if callee in PARALLEL_CALLEES and args is not None:
                arms = [Arm("", items) for items in _parallel_arms(args, language) if items]
                if len(arms) >= 2:
                    found.append((current.end_byte, Flow("parallel", callee, line, arms)))
                else:
                    found.extend((current.end_byte, item) for arm in arms for item in arm.items)
                continue

            if language != "python" and callee.endswith(JS_LOOP_METHODS) and args is not None:
                fn_arg = next((a for a in args.named_children if a.type in JS_FUNCTION_NODES), None)
                if fn_arg is not None:
                    body = function_body_flow(fn_arg.child_by_field_name("body"), language)
                    target = callee.rsplit(".", 1)[0]
                    params = fn_arg.child_by_field_name("parameters") or fn_arg.child_by_field_name("parameter")
                    item = _short(_text(params).strip("()").split(",")[0], 20) or "item"
                    found.append((current.end_byte, Flow("loop", f"{item} of {target}", line, [Arm("each", body)])))
                    continue

            found.append((current.end_byte, Flow("call", callee, line)))

        stack.extend(current.named_children)

    found.sort(key=lambda pair: pair[0])
    return [flow for _, flow in found][:MAX_ITEMS]


def _parallel_arms(args: TSNode, language: str) -> list[list[Flow]]:
    """Each argument of gather(a(), b()) / element of Promise.all([a(), b()]) is one arm."""
    elements: list[TSNode] = []
    for arg in args.named_children:
        if arg.type == "array":
            elements.extend(arg.named_children)
        elif arg.type == "list_splat":
            continue
        else:
            elements.append(arg)
    return [calls_in(element, language) for element in elements]


def function_body_flow(body: TSNode | None, language: str) -> list[Flow]:
    """Flow of a function body (a block, or a JS arrow function's expression)."""
    if body is None:
        return []
    if body.type in {"block", "statement_block", "module", "program"}:
        return python_block(body) if language == "python" else js_block(body)
    return calls_in(body, language)


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #

def python_block(block: TSNode | None, depth: int = 0) -> list[Flow]:
    if block is None:
        return []
    if depth > MAX_DEPTH:
        return calls_in(block, "python")
    items: list[Flow] = []
    for stmt in block.named_children:
        items.extend(_python_statement(stmt, depth))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _python_statement(stmt: TSNode, depth: int) -> list[Flow]:
    kind = stmt.type
    line = stmt.start_point[0] + 1
    field = stmt.child_by_field_name

    if kind in NESTED_DEFINITIONS or kind in {"import_statement", "import_from_statement", "comment"}:
        return []

    if kind == "if_statement":
        condition = field("condition")
        arms = [Arm(_short(_text(condition)), python_block(field("consequence"), depth + 1))]
        for alt in stmt.children_by_field_name("alternative"):
            if alt.type == "elif_clause":
                cond = alt.child_by_field_name("condition")
                arm_items = calls_in(cond, "python") + python_block(alt.child_by_field_name("consequence"), depth + 1)
                arms.append(Arm(_short(_text(cond)), arm_items))
            elif alt.type == "else_clause":
                arms.append(Arm("else", python_block(alt.child_by_field_name("body"), depth + 1)))
        return calls_in(condition, "python") + [Flow("if", arms[0].label, line, arms)]

    if kind == "match_statement":
        subject = field("subject")
        arms = []
        body = field("body")
        for case in (body.named_children if body is not None else []):
            if case.type == "case_clause":
                pattern = next((c for c in case.named_children if c.type == "case_pattern"), None)
                label = _short(_text(pattern)) or "_"
                arms.append(Arm("else" if label == "_" else f"case {label}",
                                python_block(case.child_by_field_name("consequence"), depth + 1)))
        return calls_in(subject, "python") + [Flow("switch", _short(_text(subject)), line, arms)]

    if kind == "try_statement":
        arms = [Arm("try", python_block(field("body"), depth + 1))]
        finally_items: list[Flow] = []
        for child in stmt.named_children:
            if child.type in {"except_clause", "except_group_clause"}:
                value = child.child_by_field_name("value")
                handler_body = next((c for c in reversed(child.named_children) if c.type == "block"), None)
                arms.append(Arm(_short(_text(value)) or "error", python_block(handler_body, depth + 1)))
            elif child.type == "finally_clause":
                finally_body = next((c for c in child.named_children if c.type == "block"), None)
                finally_items = python_block(finally_body, depth + 1)
        return [Flow("try", "try", line, arms)] + finally_items

    if kind == "for_statement":
        right = field("right")
        label = f"{_short(_text(field('left')), 30)} in {_short(_text(right), 40)}"
        return calls_in(right, "python") + [Flow("loop", label, line, [Arm("each", python_block(field("body"), depth + 1))])]

    if kind == "while_statement":
        condition = field("condition")
        return [Flow("loop", f"while {_short(_text(condition), 60)}", line,
                     [Arm("each", calls_in(condition, "python") + python_block(field("body"), depth + 1))])]

    if kind == "with_statement":
        header = next((c for c in stmt.named_children if c.type == "with_clause"), None)
        return calls_in(header, "python") + python_block(field("body"), depth + 1)

    if kind == "return_statement":
        return calls_in(stmt, "python") + [Flow("return", _short(_text(stmt)), line)]

    if kind == "raise_statement":
        return [Flow("raise", _short(_text(stmt)), line)]

    return calls_in(stmt, "python")


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------------- #

def js_block(node: TSNode | None, depth: int = 0) -> list[Flow]:
    """Flow of a statement block, or of a single statement used as a body."""
    if node is None:
        return []
    if depth > MAX_DEPTH:
        return calls_in(node, "javascript")
    statements = node.named_children if node.type in {"statement_block", "program"} else [node]
    items: list[Flow] = []
    for stmt in statements:
        items.extend(_js_statement(stmt, depth))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _js_statement(stmt: TSNode, depth: int) -> list[Flow]:
    kind = stmt.type
    line = stmt.start_point[0] + 1
    field = stmt.child_by_field_name
    lang = "javascript"

    if kind in NESTED_DEFINITIONS or kind in {"import_statement", "comment", "empty_statement"}:
        return []

    if kind == "statement_block":
        return js_block(stmt, depth + 1)

    if kind == "if_statement":
        condition = field("condition")
        arms = [Arm(_short(_text(condition).strip("()")), js_block(field("consequence"), depth + 1))]
        alternative = field("alternative")
        # Flatten `else if` chains into extra arms.
        while alternative is not None:
            inner = alternative.named_children[0] if alternative.named_children else None
            if inner is not None and inner.type == "if_statement":
                cond = inner.child_by_field_name("condition")
                arms.append(Arm(_short(_text(cond).strip("()")),
                                calls_in(cond, lang) + js_block(inner.child_by_field_name("consequence"), depth + 1)))
                alternative = inner.child_by_field_name("alternative")
            else:
                arms.append(Arm("else", js_block(inner, depth + 1)))
                alternative = None
        return calls_in(condition, lang) + [Flow("if", arms[0].label, line, arms)]

    if kind == "switch_statement":
        value = field("value")
        arms = []
        body = field("body")
        for case in (body.named_children if body is not None else []):
            statements = case.children_by_field_name("body")
            case_items = [item for s in statements for item in _js_statement(s, depth + 1)]
            if case.type == "switch_default":
                arms.append(Arm("else", case_items))
            elif case.type == "switch_case":
                arms.append(Arm(f"case {_short(_text(case.child_by_field_name('value')), 30)}", case_items))
        return calls_in(value, lang) + [Flow("switch", _short(_text(value).strip("()")), line, arms)]

    if kind == "try_statement":
        arms = [Arm("try", js_block(field("body"), depth + 1))]
        handler = field("handler")
        if handler is not None:
            arms.append(Arm(_short(_text(handler.child_by_field_name("parameter"))) or "error",
                            js_block(handler.child_by_field_name("body"), depth + 1)))
        finalizer = field("finalizer")
        finally_items = js_block(finalizer.child_by_field_name("body"), depth + 1) if finalizer is not None else []
        return [Flow("try", "try", line, arms)] + finally_items

    if kind == "for_in_statement":
        right = field("right")
        label = f"{_short(_text(field('left')), 30)} of {_short(_text(right), 40)}"
        return calls_in(right, lang) + [Flow("loop", label, line, [Arm("each", js_block(field("body"), depth + 1))])]

    if kind in {"for_statement", "while_statement", "do_statement"}:
        condition = field("condition")
        label = f"while {_short(_text(condition).strip('()'), 50)}" if condition is not None else "loop"
        return [Flow("loop", label, line, [Arm("each", js_block(field("body"), depth + 1))])]

    if kind == "return_statement":
        return calls_in(stmt, lang) + [Flow("return", _short(_text(stmt)), line)]

    if kind == "throw_statement":
        return [Flow("raise", _short(_text(stmt)), line)]

    if kind in {"export_statement", "labeled_statement"}:
        inner = field("declaration") or field("body")
        return _js_statement(inner, depth) if inner is not None else calls_in(stmt, lang)

    return calls_in(stmt, lang)


def iter_calls(items: list[Flow]):
    """Yield every callee in a flow tree, in order (used for agent tools)."""
    stack = list(reversed(items))
    while stack:
        item = stack.pop()
        if item.kind == "call":
            yield item.label
        for arm in reversed(item.arms):
            stack.extend(reversed(arm.items))
