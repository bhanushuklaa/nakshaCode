"""Safe zip extraction.

Uploaded archives are untrusted, so we never execute anything and we guard
against zip-slip paths, symlinks, zip bombs and oversized repositories.
"""

import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "venv", ".venv",
    "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist", "build",
    ".next", ".nuxt", ".output", "coverage", ".idea", ".vscode", "target", "vendor",
    ".gradle", ".terraform", "site-packages", ".cache", "out",
}
IGNORED_SUFFIXES = {
    ".lock", ".min.js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".svg", ".pdf", ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".mov", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class",
    ".jar", ".bin", ".db", ".sqlite", ".parquet",
}
IGNORED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", ".DS_Store"}


class IngestError(ValueError):
    """Raised when an archive is invalid or exceeds the configured limits."""


@dataclass(frozen=True)
class SourceFile:
    path: str        # repo-relative POSIX path, e.g. "app/main.py"
    abs_path: Path
    size: int


@dataclass(frozen=True)
class IngestResult:
    root: Path
    files: list[SourceFile]
    skipped: int


def _is_ignored(parts: tuple[str, ...]) -> bool:
    name = parts[-1]
    if name in IGNORED_NAMES or any(p in IGNORED_DIRS for p in parts[:-1]):
        return True
    lower = name.lower()
    return any(lower.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def _strip_common_root(names: list[str]) -> str:
    """Zips from GitHub wrap everything in one folder; return that prefix."""
    tops = {PurePosixPath(n).parts[0] for n in names if n.strip("/")}
    if len(tops) == 1 and all("/" in n for n in names if n.strip("/")):
        return tops.pop() + "/"
    return ""


def extract_zip(
    zip_path: Path,
    dest: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
    max_file_bytes: int,
) -> IngestResult:
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise IngestError("The upload is not a valid zip file.") from exc

    dest = dest.resolve()
    files: list[SourceFile] = []
    skipped = 0
    total = 0

    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        prefix = _strip_common_root([i.filename for i in infos])

        for info in infos:
            name = info.filename[len(prefix):] if prefix else info.filename
            rel = PurePosixPath(name)

            # Reject absolute paths, ".." segments and symlinks.
            is_symlink = stat.S_ISLNK(info.external_attr >> 16)
            if rel.is_absolute() or ".." in rel.parts or is_symlink or not rel.parts:
                skipped += 1
                continue
            if _is_ignored(rel.parts) or info.file_size > max_file_bytes:
                skipped += 1
                continue

            if len(files) >= max_files:
                raise IngestError(f"Too many source files (limit {max_files}).")
            total += info.file_size
            if total > max_uncompressed_bytes:
                raise IngestError("Archive is too large once uncompressed.")

            target = (dest / rel).resolve()
            if not target.is_relative_to(dest):
                skipped += 1
                continue
            with archive.open(info) as src:
                # Read at most the limit + 1 byte to defeat lying size headers.
                data = src.read(max_file_bytes + 1)
            if len(data) > max_file_bytes:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append(SourceFile(path=str(rel), abs_path=target, size=len(data)))

    if not files:
        raise IngestError("No source files found in the archive.")
    files.sort(key=lambda f: f.path)
    return IngestResult(root=dest, files=files, skipped=skipped)
