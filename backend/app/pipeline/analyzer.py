"""Run the deterministic analysis pipeline on an extracted repository."""

import os
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from ..schema import AnalysisResult, FileEntry, Stats
from .codeindex import CodeIndex
from .ingest import IngestResult
from .overview import build_overview
from .parser import ParsedFile, detect_language, parse_file
from .resolver import Resolver
from .triggers import find_triggers
from .workflows import WorkflowBuilder

Reporter = Callable[[str, float], None]  # (message, progress 0..1)
MAX_WORKFLOWS = 40
PARALLEL_MIN_FILES = 150   # below this, starting worker processes costs more than it saves
MAX_PARSE_WORKERS = 8


def _parse_one(item: tuple[str, str]) -> ParsedFile:
    """Top-level so worker processes can pickle it."""
    path, abs_path = item
    with open(abs_path, "rb") as fh:
        return parse_file(path, fh.read())


def _parse_all(files, report: Reporter) -> list[ParsedFile]:
    """Parse files on all CPU cores for big repos, in this process for small ones.

    tree-sitter walking is pure Python and holds the GIL, so threads would not
    help; separate processes do. "spawn" is used because forking a process
    that already runs threads (the job pool) is unsafe.
    """
    items = [(f.path, str(f.abs_path)) for f in files]
    workers = min(os.cpu_count() or 2, MAX_PARSE_WORKERS)
    if len(items) < PARALLEL_MIN_FILES or workers < 2:
        parsed = []
        for i, item in enumerate(items, start=1):
            parsed.append(_parse_one(item))
            if i % 200 == 0:
                report(f"Parsed {i}/{len(items)} files", 0.10 + 0.30 * i / len(items))
        return parsed

    report(f"Parsing {len(items)} files on {workers} CPU cores", 0.12)
    parsed = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        for i, result in enumerate(pool.map(_parse_one, items, chunksize=16), start=1):
            parsed.append(result)
            if i % 200 == 0:
                report(f"Parsed {i}/{len(items)} files", 0.10 + 0.30 * i / len(items))
    return parsed


def _display_package(language: str, package: str) -> str:
    return package.split(".")[0] if language == "python" else package


def analyze_repository(ingested: IngestResult, project: str, report: Reporter) -> tuple[AnalysisResult, CodeIndex]:
    files = [f for f in ingested.files if detect_language(f.path)]
    report(f"Reading {len(files)} files", 0.10)

    parsed = _parse_all(files, report)

    warnings = [f"{p.path}: {p.error}" for p in parsed if p.error][:20]
    report("Resolving imports and building the file graph", 0.45)
    index = CodeIndex(parsed, Resolver(ingested.root, [p.path for p in parsed]))
    imported_by = index.imported_by()

    report("Detecting triggers and building workflows", 0.55)
    triggers = find_triggers([p for p in parsed if p.parsed])
    builder = WorkflowBuilder(index)
    workflows = []
    for trigger in triggers:
        graph = builder.build(trigger)
        if trigger.kind == "entry" and len(graph.nodes) == 1:
            continue  # a guessed entry point with no steps is just noise
        workflows.append(graph)

    # Richest workflows first: more steps and more services = more to explain.
    workflows.sort(key=lambda g: (-len(g.nodes), -sum(len(n.services) for n in g.nodes), g.title))
    all_workflows = workflows
    if len(workflows) > MAX_WORKFLOWS:
        warnings.append(f"Showing the {MAX_WORKFLOWS} richest of {len(workflows)} workflows.")
        workflows = workflows[:MAX_WORKFLOWS]

    entries: list[FileEntry] = []
    for p in parsed:
        links = index.links.get(p.path)
        entries.append(FileEntry(
            path=p.path,
            language=p.language,
            lines=p.lines,
            size=p.size,
            parsed=p.parsed,
            summary=next((fn.doc for fn in p.functions if fn.doc), ""),
            functions=[fn.qualname for fn in p.functions if not fn.synthetic][:50],
            imports=sorted(links.imports) if links else [],
            imported_by=sorted(imported_by.get(p.path, [])),
            packages=sorted({_display_package(p.language, pkg) for pkg in links.packages}) if links else [],
            services=[i.label for i in index.file_integrations(p.path)],
        ))

    stats = Stats(
        files_total=len(entries),
        files_parsed=sum(1 for e in entries if e.parsed),
        lines_total=sum(e.lines for e in entries),
        languages=dict(Counter(e.language for e in entries).most_common()),
        skipped=ingested.skipped,
        workflows=len(workflows),
    )
    # The whole-project overview comes first; it is built from every workflow, not just the shown ones.
    overview = build_overview(project, all_workflows, entries)
    shown = [overview, *workflows] if overview and len(overview.nodes) > 1 else workflows
    result = AnalysisResult(project=project, stats=stats, files=entries,
                            workflows=shown, warnings=warnings)
    report(f"Found {len(workflows)} workflows in {stats.files_parsed} parsed files", 0.65)
    return result, index


def file_sha_map(index: CodeIndex) -> dict[str, str]:
    return {path: f.sha for path, f in index.files.items()}

