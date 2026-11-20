"""Static code parsing with tree-sitter."""

import hashlib
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from tree_sitter import Node as TSNode
from tree_sitter import Parser
from tree_sitter_language_pack import get_parser

from .flow import Flow, function_body_flow, js_block, python_block

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
}
# Files we cannot parse yet but still show in the file list and map view.
OTHER_LANGUAGES = {
    ".go": "go", ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".swift": "swift", ".vue": "vue", ".svelte": "svelte", ".html": "html",
    ".css": "css", ".scss": "css", ".sql": "sql", ".sh": "shell", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "all"}
ROUTER_OBJECT = re.compile(r"^(app|router|server|api|fastify|routes?|\w*[Rr]outer)$")
MAX_CALLS_PER_FUNCTION = 200


@dataclass
class FunctionInfo:
    name: str
    qualname: str
    line: int
    params: str = ""
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # callee text, in source order
    doc: str = ""
    synthetic: bool = False       # e.g. "<module>" or an inline route handler
    trigger_hint: str = ""        # set by the parser for patterns like app.get("/x", fn)
    flow: list[Flow] = field(default_factory=list)  # control-flow tree (see flow.py)

    def add_call(self, callee: str) -> None:
        if callee and len(self.calls) < MAX_CALLS_PER_FUNCTION and callee not in self.calls:
            self.calls.append(callee)


@dataclass
class ParsedFile:
    path: str
    language: str
    lines: int
    size: int
    sha: str
    parsed: bool
    imports: list[str] = field(default_factory=list)            # raw module specifiers
    bindings: dict[str, str] = field(default_factory=dict)      # local name -> module
    assignments: dict[str, str] = field(default_factory=dict)   # module var -> callee text
    functions: list[FunctionInfo] = field(default_factory=list)
    route_prefixes: dict[str, str] = field(default_factory=dict)  # router var -> URL prefix
    error: str = ""


def detect_language(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return LANGUAGE_BY_SUFFIX.get(suffix) or OTHER_LANGUAGES.get(suffix)


@cache
def _parser_for(language: str) -> Parser:
    return get_parser(language)


def _text(node: TSNode | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _string_value(node: TSNode | None) -> str | None:
    """Return the literal value of a string node, or None if it is not a string."""
    if node is None or node.type not in {"string", "template_string"}:
        return None
    return _text(node).strip("'\"`")


def _short(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]


ROUTER_PREFIX = re.compile(
    r"^\s*(\w+)\s*=\s*(?:\w+\.)?(?:APIRouter|Blueprint)\((?P<args>[^)]*)\)", re.M)
PREFIX_ARG = re.compile(r"(?:url_)?prefix\s*=\s*[rf]?['\"]([^'\"]*)['\"]")


def _route_prefixes(text: str) -> dict[str, str]:
    """FastAPI/Flask routers declared with a URL prefix, e.g. router = APIRouter(prefix="/training")."""
    found = {}
    for match in ROUTER_PREFIX.finditer(text):
        if prefix := PREFIX_ARG.search(match["args"]):
            found[match.group(1)] = prefix.group(1)
    return found


def parse_file(path: str, data: bytes) -> ParsedFile:
    language = detect_language(path) or "other"
    text_lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    result = ParsedFile(
        path=path,
        language=language,
        lines=text_lines,
        size=len(data),
        sha=hashlib.sha256(data).hexdigest(),
        parsed=language in LANGUAGE_BY_SUFFIX.values(),
    )
    if not result.parsed:
        return result

    try:
        tree = _parser_for(language).parse(data)
        module_fn = FunctionInfo(name="<module>", qualname="<module>", line=1, synthetic=True)
        result.functions.append(module_fn)
        if language == "python":
            result.route_prefixes = _route_prefixes(data.decode("utf-8", errors="replace"))
            _walk_python(tree.root_node, result, module_fn)
            module_fn.flow = python_block(tree.root_node)
        else:
            _walk_js(tree.root_node, result, module_fn)
            module_fn.flow = js_block(tree.root_node)
    except Exception as exc:  # a broken file must never break the whole analysis
        result.error = f"parse error: {exc}"
    return result


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #

def _python_docstring(body: TSNode | None) -> str:
    if body is None or not body.named_children:
        return ""
    first = body.named_children[0]
    # Older grammars wrap statements in expression_statement; newer ones do not.
    if first.type == "expression_statement" and first.named_children:
        first = first.named_children[0]
    if first.type == "string":
        return _short(_text(first).strip("\"' \n"), 200)
    return ""


def _python_import(node: TSNode, out: ParsedFile) -> None:
    if node.type == "import_statement":
        for child in node.named_children:
            if child.type == "aliased_import":
                module = _text(child.child_by_field_name("name"))
                out.bindings[_text(child.child_by_field_name("alias"))] = module
            else:
                module = _text(child)
                out.bindings[module.split(".")[0]] = module.split(".")[0]
            out.imports.append(module)
        return

    # from X import a, b as c
    module_node = node.child_by_field_name("module_name")
    module = _text(module_node)
    out.imports.append(module)
    for name_node in node.children_by_field_name("name"):
        if name_node.type == "aliased_import":
            name = _text(name_node.child_by_field_name("name"))
            local = _text(name_node.child_by_field_name("alias"))
        else:
            name = local = _text(name_node)
        # Bind to "module:name" so the resolver can find submodules or symbols.
        out.bindings[local] = f"{module}:{name}"


def _walk_python(root: TSNode, out: ParsedFile, module_fn: FunctionInfo) -> None:
    # Stack items: (node, current function, enclosing class name, pending decorators)
    stack: list[tuple[TSNode, FunctionInfo, str, list[str]]] = [(root, module_fn, "", [])]

    while stack:
        node, fn, cls, decorators = stack.pop()
        kind = node.type

        if kind in {"import_statement", "import_from_statement"}:
            _python_import(node, out)
            continue

        if kind == "decorated_definition":
            decos = [_short(_text(d).lstrip("@")) for d in node.named_children if d.type == "decorator"]
            definition = node.child_by_field_name("definition")
            if definition is not None:
                stack.append((definition, fn, cls, decos))
            continue

        if kind == "function_definition":
            name = _text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            new_fn = FunctionInfo(
                name=name,
                qualname=f"{cls}.{name}" if cls else name,
                line=node.start_point[0] + 1,
                params=_short(_text(node.child_by_field_name("parameters")).strip("()"), 80),
                decorators=decorators,
                doc=_python_docstring(body),
                flow=python_block(body),
            )
            out.functions.append(new_fn)
            if body is not None:
                stack.extend((c, new_fn, "", []) for c in reversed(body.named_children))
            continue

        if kind == "class_definition":
            name = _text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            if body is not None:
                stack.extend((c, fn, name, []) for c in reversed(body.named_children))
            continue

        if kind == "if_statement" and fn is module_fn:
            condition = _text(node.child_by_field_name("condition"))
            if "__name__" in condition and "__main__" in condition:
                main_fn = FunctionInfo(
                    name="__main__", qualname="__main__", line=node.start_point[0] + 1,
                    synthetic=True, trigger_hint="cli:main",
                    flow=python_block(node.child_by_field_name("consequence")),
                )
                out.functions.append(main_fn)
                stack.extend((c, main_fn, "", []) for c in reversed(node.named_children))
                continue

        if kind == "call":
            fn.add_call(_short(_text(node.child_by_field_name("function")), 80))

        if kind == "assignment" and fn is module_fn:
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None and left.type == "identifier" and right.type == "call":
                out.assignments[_text(left)] = _short(_text(right.child_by_field_name("function")), 80)

        stack.extend((c, fn, cls, []) for c in reversed(node.named_children))


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------------- #

FUNCTION_NODES = {"arrow_function", "function_expression", "function", "generator_function"}


def _js_bind_require(name_node: TSNode, module: str, out: ParsedFile) -> None:
    """Handle `const x = require("m")` and `const { a, b: c } = require("m")`."""
    if name_node.type == "identifier":
        out.bindings[_text(name_node)] = module
        return
    for child in name_node.named_children:
        if child.type == "shorthand_property_identifier_pattern":
            out.bindings[_text(child)] = f"{module}:{_text(child)}"
        elif child.type == "pair_pattern":
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            out.bindings[_text(value)] = f"{module}:{_text(key)}"


def _js_declarator_value(name_node: TSNode, value: TSNode, out: ParsedFile, at_module_level: bool) -> bool:
    """Record `x = require("m")` imports and module-level clients like `pool = new Pool()`.

    Returns True when the value was a require() call (fully handled).
    """
    if value.type == "await_expression" and value.named_children:
        value = value.named_children[0]

    if value.type == "call_expression":
        callee = _text(value.child_by_field_name("function"))
        args = value.child_by_field_name("arguments")
        if callee == "require" and args is not None and args.named_children:
            module = _string_value(args.named_children[0])
            if module:
                out.imports.append(module)
                _js_bind_require(name_node, module, out)
                return True
    elif value.type == "new_expression":
        callee = _text(value.child_by_field_name("constructor"))
    else:
        return False

    if at_module_level and name_node.type == "identifier":
        out.assignments[_text(name_node)] = _short(callee, 80)
    return False


def _js_import(node: TSNode, out: ParsedFile) -> None:
    module = _string_value(node.child_by_field_name("source"))
    if module is None:
        return
    out.imports.append(module)
    clause = next((c for c in node.named_children if c.type == "import_clause"), None)
    if clause is None:
        return
    for child in clause.named_children:
        if child.type == "identifier":  # default import
            out.bindings[_text(child)] = module
        elif child.type == "namespace_import":
            for ident in child.named_children:
                out.bindings[_text(ident)] = module
        elif child.type == "named_imports":
            for spec in child.named_children:
                name = _text(spec.child_by_field_name("name"))
                alias = spec.child_by_field_name("alias")
                out.bindings[_text(alias) if alias else name] = f"{module}:{name}"


def _js_route_call(node: TSNode) -> tuple[str, list[TSNode]] | None:
    """Detect app.get("/path", handler) style registrations.

    Returns (trigger hint, argument nodes) or None.
    """
    callee = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if callee is None or args is None or callee.type != "member_expression":
        return None
    obj = _text(callee.child_by_field_name("object"))
    prop = _text(callee.child_by_field_name("property"))
    arg_nodes = list(args.named_children)
    first = _string_value(arg_nodes[0]) if arg_nodes else None

    is_path = first is not None and first.startswith("/")
    if prop in HTTP_METHODS and is_path and len(arg_nodes) > 1 and ROUTER_OBJECT.match(obj.split(".")[-1]):
        return f"http:{prop.upper()}:{first}", arg_nodes[1:]
    if prop == "schedule" and "cron" in obj.lower():
        return f"schedule:{first or ''}", arg_nodes[1:]
    if prop == "process" and arg_nodes:  # Bull / BullMQ style queue processors
        return f"queue:{obj}", arg_nodes
    return None


def _walk_js(root: TSNode, out: ParsedFile, module_fn: FunctionInfo) -> None:
    stack: list[tuple[TSNode, FunctionInfo, str]] = [(root, module_fn, "")]

    language = out.language

    def new_function(name: str, node: TSNode, cls: str = "", **extra) -> FunctionInfo:
        fn_info = FunctionInfo(
            name=name,
            qualname=f"{cls}.{name}" if cls else name,
            line=node.start_point[0] + 1,
            params=_short(_text(node.child_by_field_name("parameters")).strip("()"), 80),
            flow=function_body_flow(node.child_by_field_name("body"), language),
            **extra,
        )
        out.functions.append(fn_info)
        return fn_info

    while stack:
        node, fn, cls = stack.pop()
        kind = node.type

        if kind == "import_statement":
            _js_import(node, out)
            continue

        if kind in {"function_declaration", "generator_function_declaration"}:
            new_fn = new_function(_text(node.child_by_field_name("name")), node)
            body = node.child_by_field_name("body")
            if body is not None:
                stack.append((body, new_fn, ""))
            continue

        if kind in {"class_declaration", "class"}:
            name = _text(node.child_by_field_name("name")) or cls
            body = node.child_by_field_name("body")
            if body is not None:
                stack.extend((c, fn, name) for c in reversed(body.named_children))
            continue

        if kind == "method_definition":
            decorators = [_short(_text(d).lstrip("@")) for d in node.named_children if d.type == "decorator"]
            # TypeScript puts method decorators as preceding siblings in the class body.
            sibling = node.prev_named_sibling
            while sibling is not None and sibling.type == "decorator":
                decorators.insert(0, _short(_text(sibling).lstrip("@")))
                sibling = sibling.prev_named_sibling
            new_fn = new_function(_text(node.child_by_field_name("name")), node, cls, decorators=decorators)
            body = node.child_by_field_name("body")
            if body is not None:
                stack.append((body, new_fn, ""))
            continue

        if kind == "variable_declarator":
            name_node, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            if name_node is not None and value is not None:
                if value.type in FUNCTION_NODES:
                    new_fn = new_function(_text(name_node), value)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        stack.append((body, new_fn, ""))
                    continue
                if _js_declarator_value(name_node, value, out, at_module_level=fn is module_fn):
                    continue  # it was a require(); nothing else to walk

        if kind == "assignment_expression" and fn is module_fn:
            # exports.handler = async (event) => { ... }  (AWS Lambda style)
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None and right.type in FUNCTION_NODES:
                name = _text(left).split(".")[-1]
                new_fn = new_function(name, right)
                body = right.child_by_field_name("body")
                if body is not None:
                    stack.append((body, new_fn, ""))
                continue

        if kind == "call_expression":
            route = _js_route_call(node)
            if route is not None:
                hint, handler_args = route
                route_fn = FunctionInfo(
                    name=hint.split(":", 1)[1] or hint, qualname=hint, line=node.start_point[0] + 1,
                    synthetic=True, trigger_hint=hint,
                )
                out.functions.append(route_fn)
                for arg in handler_args:
                    if arg.type in FUNCTION_NODES:
                        body = arg.child_by_field_name("body")
                        route_fn.flow.extend(function_body_flow(body, language))
                        if body is not None:
                            stack.append((body, route_fn, ""))
                    elif arg.type in {"identifier", "member_expression"}:
                        route_fn.add_call(_text(arg))
                        route_fn.flow.append(Flow("call", _text(arg), arg.start_point[0] + 1))
                continue
            fn.add_call(_short(_text(node.child_by_field_name("function")), 80))

        if kind == "new_expression":
            fn.add_call(_short(_text(node.child_by_field_name("constructor")), 80))

        stack.extend((c, fn, cls) for c in reversed(node.named_children))
