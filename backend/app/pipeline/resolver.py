"""Turn raw import specifiers into repo files (internal) or package names (external)."""

import json
import posixpath
import re
import sys
from pathlib import Path, PurePosixPath

from .integrations import match_package
from .parser import ParsedFile

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
PY_STDLIB = set(sys.stdlib_module_names)
NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "events", "stream",
    "child_process", "buffer", "zlib", "net", "tls", "dns", "assert", "querystring",
    "readline", "worker_threads", "cluster", "timers", "process", "module", "v8",
}


Alias = tuple[str, str, str]  # (folder the config applies to, import prefix, target folder)


def _read_jsonc(path: Path) -> dict:
    """Read tsconfig-style JSON, which allows comments and trailing commas."""
    raw = path.read_text(errors="replace")
    for text in (raw, re.sub(r",(\s*[}\]])", r"\1", re.sub(r"/\*.*?\*/", "", re.sub(r"^\s*//.*$", "", raw, flags=re.M), flags=re.S))):
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _folder(root: Path, path: Path) -> str:
    rel = path.parent.relative_to(root).as_posix()
    return "" if rel == "." else rel


def _load_ts_aliases(root: Path) -> list[Alias]:
    """Collect import aliases from every tsconfig*/jsconfig in the repo.

    Monorepos keep these in sub-folders (e.g. frontend/tsconfig.app.json), so we
    read them all and scope each alias to the folder its config lives in.
    Packages without aliases get the common "@/" and "~/" -> "src/" defaults.
    """
    aliases: list[Alias] = []
    configured: set[str] = set()
    for config_file in sorted([*root.rglob("tsconfig*.json"), *root.rglob("jsconfig.json")]):
        scope = _folder(root, config_file)
        options = _read_jsonc(config_file).get("compilerOptions") or {}
        base = posixpath.normpath(posixpath.join(scope, options.get("baseUrl", ".")))
        base = "" if base == "." else base
        for alias, targets in (options.get("paths") or {}).items():
            if targets:
                target = posixpath.normpath(posixpath.join(base, targets[0].rstrip("*")))
                aliases.append((scope, alias.rstrip("*"), "" if target == "." else target + "/"))
                configured.add(scope)
        if "baseUrl" in options:  # bare imports like "components/Button" resolve from baseUrl
            aliases.append((scope, "", base + "/" if base else ""))
            configured.add(scope)

    for package_file in root.rglob("package.json"):
        scope = _folder(root, package_file)
        if scope not in configured:
            src = f"{scope}/src/" if scope else "src/"
            aliases += [(scope, "@/", src), (scope, "~/", src)]
    if not aliases:
        aliases = [("", "@/", "src/"), ("", "~/", "src/")]
    # Most specific first: deepest folder, then longest prefix.
    aliases.sort(key=lambda a: (len(a[0]), len(a[1])), reverse=True)
    return aliases


class Resolver:
    def __init__(self, root: Path, paths: list[str]):
        self.paths = set(paths)
        self.ts_aliases = _load_ts_aliases(root)
        self.py_modules = self._index_python_modules(paths)

    # ------------------------------------------------------------------ python
    @staticmethod
    def _index_python_modules(paths: list[str]) -> dict[str, str]:
        """Map dotted module names to files, for the repo root and every parent dir.

        "src/shop/db.py" is reachable as "src.shop.db", "shop.db" and "db";
        longer names win when two files compete for the same short name.
        """
        index: dict[str, tuple[int, str]] = {}
        for path in paths:
            if not path.endswith(".py"):
                continue
            parts = list(PurePosixPath(path[:-3]).parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for start in range(len(parts)):
                dotted = ".".join(parts[start:])
                depth = len(parts) - start
                if dotted and (dotted not in index or index[dotted][0] < depth):
                    index[dotted] = (depth, path)
        return {name: path for name, (_, path) in index.items()}

    def _python_relative(self, importer: str, module: str) -> str:
        dots = len(module) - len(module.lstrip("."))
        package = PurePosixPath(importer).parent
        for _ in range(dots - 1):
            package = package.parent
        rest = module.lstrip(".")
        base = ".".join(p for p in package.parts if p)
        return f"{base}.{rest}".strip(".") if rest else base

    def resolve_python(self, importer: str, module: str, symbol: str = "") -> str | None:
        dotted = self._python_relative(importer, module) if module.startswith(".") else module
        candidates = [f"{dotted}.{symbol}", dotted] if symbol else [dotted]
        for candidate in candidates:
            if candidate in self.py_modules:
                return self.py_modules[candidate]
        return None

    # ---------------------------------------------------------------------- js
    def _js_try(self, base: str) -> str | None:
        base = posixpath.normpath(base)
        if base in self.paths:
            return base
        stem = base
        for ext in JS_EXTENSIONS:
            if stem.endswith(ext):  # "./x.js" written for a "./x.ts" file
                stem = stem[: -len(ext)]
                break
        for ext in JS_EXTENSIONS:
            if stem + ext in self.paths:
                return stem + ext
        for ext in JS_EXTENSIONS:
            if f"{base}/index{ext}" in self.paths:
                return f"{base}/index{ext}"
        return None

    def resolve_js(self, importer: str, module: str) -> str | None:
        if module.startswith("."):
            return self._js_try(posixpath.join(posixpath.dirname(importer), module))
        for scope, prefix, target in self.ts_aliases:
            if scope and not importer.startswith(scope + "/"):
                continue  # this alias belongs to another package in the repo
            if module.startswith(prefix):
                found = self._js_try(target + module[len(prefix):])
                if found:
                    return found
        return None

    # ----------------------------------------------------------------- public
    def resolve(self, file: ParsedFile, spec: str) -> str | None:
        """Resolve a binding value ("module" or "module:symbol") to a repo file."""
        module, _, symbol = spec.partition(":")
        if file.language == "python":
            return self.resolve_python(file.path, module, symbol)
        return self.resolve_js(file.path, module)

    @staticmethod
    def package_name(language: str, module: str) -> str | None:
        """Top-level external package for a module, or None for stdlib/relative."""
        module = module.partition(":")[0]
        if not module or module.startswith((".", "/")):
            return None
        if language == "python":
            top = module.split(".")[0]
            # Standard-library modules are not packages, except ones that reach
            # an external service (smtplib, urllib.request, sqlite3, ...).
            if top in PY_STDLIB and match_package(module) is None:
                return None
            return module
        if module.startswith("node:"):
            return None
        parts = module.split("/")
        name = "/".join(parts[:2]) if module.startswith("@") else parts[0]
        return None if name in NODE_BUILTINS else name
