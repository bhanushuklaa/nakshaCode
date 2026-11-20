"""Cross-file index: resolved imports, the file graph, symbols and call resolution."""

from collections import defaultdict
from dataclasses import dataclass, field

from .integrations import Integration, match_package
from .parser import FunctionInfo, ParsedFile
from .resolver import Resolver

# Very generic names are never resolved globally (too many false matches).
AMBIGUOUS_NAMES = {
    "get", "set", "run", "main", "init", "start", "stop", "update", "create", "delete",
    "save", "load", "send", "close", "open", "read", "write", "handle", "process",
    "call", "execute", "apply", "map", "filter", "find", "push", "add", "remove",
    "then", "catch", "log", "print", "len", "str", "int", "dict", "list", "json",
}


@dataclass
class FileLinks:
    imports: set[str] = field(default_factory=set)           # repo files
    packages: set[str] = field(default_factory=set)          # external packages
    local_bindings: dict[str, str] = field(default_factory=dict)    # name -> repo file
    package_bindings: dict[str, str] = field(default_factory=dict)  # name -> package


FnRef = tuple[str, FunctionInfo]  # (file path, function)


class CodeIndex:
    def __init__(self, files: list[ParsedFile], resolver: Resolver):
        self.files = {f.path: f for f in files}
        self.links: dict[str, FileLinks] = {}
        self.functions_by_file: dict[str, dict[str, FunctionInfo]] = {}
        self.functions_by_name: dict[str, list[FnRef]] = defaultdict(list)

        for f in files:
            if f.parsed:
                self.links[f.path] = self._link_file(f, resolver)
                self._index_functions(f)

    # ------------------------------------------------------------ building
    @staticmethod
    def _link_file(f: ParsedFile, resolver: Resolver) -> FileLinks:
        links = FileLinks()
        for spec in f.imports:
            target = resolver.resolve(f, spec)
            if target and target != f.path:
                links.imports.add(target)
            elif not target and (pkg := resolver.package_name(f.language, spec)):
                links.packages.add(pkg)
        for local, spec in f.bindings.items():
            target = resolver.resolve(f, spec)
            if target and target != f.path:
                links.local_bindings[local] = target
                links.imports.add(target)
            elif not target and (pkg := resolver.package_name(f.language, spec)):
                links.package_bindings[local] = pkg
        return links

    def _index_functions(self, f: ParsedFile) -> None:
        table: dict[str, FunctionInfo] = {}
        for fn in f.functions:
            table.setdefault(fn.qualname, fn)
            table.setdefault(fn.name, fn)
            if not fn.synthetic:
                self.functions_by_name[fn.name].append((f.path, fn))
        self.functions_by_file[f.path] = table

    # ------------------------------------------------------------- queries
    def imported_by(self) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = defaultdict(set)
        for path, links in self.links.items():
            for target in links.imports:
                reverse[target].add(path)
        return reverse

    def file_integrations(self, path: str) -> list[Integration]:
        seen: dict[str, Integration] = {}
        for pkg in self.links.get(path, FileLinks()).packages:
            if (item := match_package(pkg)) is not None:
                seen.setdefault(item.id, item)
        return list(seen.values())

    def resolve_call(self, path: str, callee: str) -> FnRef | None:
        """Find the repo function a call refers to, or None."""
        parts = callee.replace("?.", ".").split(".")
        name, head = parts[-1], parts[0]
        table = self.functions_by_file.get(path, {})
        links = self.links.get(path, FileLinks())

        if len(parts) == 1:
            if (fn := table.get(name)) is not None and not fn.synthetic:
                return path, fn
            if (target := links.local_bindings.get(name)) is not None:
                if (fn := self.functions_by_file.get(target, {}).get(name)) is not None:
                    return target, fn
        else:
            if head in {"self", "this", "cls"}:
                for fn in table.values():
                    if fn.name == name and not fn.synthetic:
                        return path, fn
            if (target := links.local_bindings.get(head)) is not None:
                target_table = self.functions_by_file.get(target, {})
                # module.func(), or func.method() such as a Celery task's .delay()
                if (fn := target_table.get(name) or target_table.get(head)) is not None:
                    return target, fn
            if (fn := table.get(head)) is not None and not fn.synthetic:
                return path, fn
            if head in links.package_bindings or head in self.files[path].assignments:
                return None  # a method on an external client, not repo code

        candidates = self.functions_by_name.get(name, [])
        if len(candidates) == 1 and name not in AMBIGUOUS_NAMES and len(name) > 3:
            return candidates[0]
        return None

    def call_integration(self, path: str, callee: str) -> Integration | None:
        """Map a call like `requests.get` or `slack.chat_postMessage` to a service."""
        links = self.links.get(path)
        if links is None:
            return None
        head = callee.replace("new ", "").split(".")[0].split("(")[0]
        package = links.package_bindings.get(head)
        if package is None:
            # Module-level client, e.g. `client = Anthropic()` then `client.messages.create`.
            created_by = self.files[path].assignments.get(head, "")
            package = links.package_bindings.get(created_by.split(".")[0])
        return match_package(package) if package else None

    def function_integrations(self, path: str, fn: FunctionInfo) -> list[Integration]:
        seen: dict[str, Integration] = {}
        for callee in fn.calls:
            if (item := self.call_integration(path, callee)) is not None:
                seen.setdefault(item.id, item)
        return list(seen.values())
