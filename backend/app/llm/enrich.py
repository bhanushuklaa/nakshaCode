"""Everything the LLM adds on top of the deterministic analysis.

The model never sees raw repositories, only compact, pre-digested summaries.
That keeps prompts short, which keeps a 30B model fast on a single GPU.

To keep a zip fast, the model does only two things up front:
  1. explain the project overview (one request), shown as soon as it is ready
  2. summarize the most important files (a few batched requests)
Individual workflows are explained on demand, when the user asks for one.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath
from typing import Any

from ..config import Settings
from ..pipeline.codeindex import CodeIndex
from ..pipeline.integrations import find_by_name
from ..pipeline.parser import detect_language, parse_file
from ..schema import AnalysisResult, Edge, Graph, Node
from . import prompts
from .cache import Cache
from .client import LLMClient, LLMError
from .validate import auto_repair, find_problems

log = logging.getLogger(__name__)
Reporter = Callable[[str, float], None]

TYPE_ICONS = {"trigger": "webhook", "start": "play", "end": "stop", "decision": "decision",
              "agent": "agent", "loop": "loop"}
_log_lock = threading.Lock()


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class Enricher:
    def __init__(self, client: LLMClient, cache: Cache, settings: Settings):
        self.client = client
        self.cache = cache
        self.settings = settings

    def _chat(self, messages: list[dict[str, str]], schema: dict, name: str, max_tokens: int | None = None) -> dict[str, Any]:
        """Call the model and, if TRAINING_LOG is on, save the exchange as training data."""
        reply = self.client.chat_json(messages, schema, name, max_tokens)
        if self.settings.training_log:
            record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": name, "model": self.settings.llm_model,
                      "messages": messages, "response": reply}
            folder = self.settings.data_dir / "training"
            folder.mkdir(parents=True, exist_ok=True)
            with _log_lock, open(folder / f"{time.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return reply

    # ---------------------------------------------------------- code mode
    def enrich_analysis(self, result: AnalysisResult, index: CodeIndex, report: Reporter,
                        publish: Callable[[], None] | None = None) -> None:
        """Explain the overview, reuse cached workflow names, summarize key files.

        `publish` pushes the result to the UI after each phase, so the overview
        appears with AI names while file summaries are still being written.
        """
        summaries = {f.path: f.summary for f in result.files}
        try:
            overview = next((w for w in result.workflows if w.overview), None)
            if overview:
                report("AI is explaining the project overview", 0.68)
                self._explain(overview, summaries, prompts.OVERVIEW_SYSTEM)
                if overview.summary:
                    result.summary = overview.summary
                if publish:
                    publish()
            # Workflows explained before (same code, same model) get their names back for free.
            for workflow in result.workflows:
                if not workflow.overview:
                    self._explain(workflow, summaries, prompts.WORKFLOW_SYSTEM, cached_only=True)
            failures: list[str] = []
            self._summarize_files(result, index, report, failures)
            if failures:
                result.warnings.append(f"AI could not summarize {len(failures)} file batch(es).")
            result.llm_used = True
        except LLMError as exc:  # the model server itself is down or answered badly
            log.warning("LLM enrichment stopped: %s", exc)
            result.warnings.append(f"AI enrichment stopped ({exc}). Showing code-derived names.")
        except Exception:  # a bug in enrichment must never throw away the code analysis
            log.exception("LLM enrichment crashed")
            result.warnings.append("AI enrichment hit an internal error. Showing code-derived names.")

    def explain_workflow(self, workflow: Graph, file_summaries: dict[str, str], report: Reporter) -> Graph:
        """Name the steps of one workflow (called when the user clicks 'Explain with AI')."""
        report("AI is reading this workflow", 0.2)
        system = prompts.OVERVIEW_SYSTEM if workflow.overview else prompts.WORKFLOW_SYSTEM
        self._explain(workflow, file_summaries, system)
        return workflow

    def _explain(self, workflow: Graph, summaries: dict[str, str], system: str, cached_only: bool = False) -> bool:
        """Ask the model for a title, summary and step labels; cached by content. Returns True if applied."""
        if len(workflow.nodes) < 2:
            return False
        payload = {
            "title": workflow.title,
            "steps": [{
                "id": n.id,
                "type": n.type,
                "label": n.label,
                "function": n.function or "",
                "file": n.file,
                "services": n.services,
                "code_or_doc": n.summary,
            } for n in workflow.nodes],
            "edges": [[e.source, e.target, e.kind] for e in workflow.edges],
        }
        # Keyed on the workflow itself (not the file summaries, which change as the AI
        # writes them), so the same code gets its names back on the next upload.
        key = Cache.key("workflow", prompts.PROMPT_VERSION, self.settings.llm_model, system[:40],
                        json.dumps(payload, sort_keys=True))
        reply = self.cache.get(key)
        if reply is None:
            if cached_only:
                return False
            for step, node in zip(payload["steps"], workflow.nodes, strict=True):
                step["file_summary"] = summaries.get(node.file or "", "")
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload)}]
            # Room for a label + summary per node, so big workflows are not cut off mid-JSON.
            max_tokens = min(8000, 600 + 110 * len(workflow.nodes))
            reply = self._chat(messages, prompts.WORKFLOW_SCHEMA, "workflow_labels", max_tokens=max_tokens)
            self.cache.set(key, reply)
        self._apply_labels(workflow, reply)
        return True

    def _run_parallel(self, tasks: list[tuple[str, Callable[[], Any]]], on_result: Callable[[str, Any], None],
                      on_progress: Callable[[int, int], None], failures: list[str]) -> None:
        """Run model requests concurrently; apply each answer as it arrives.

        Answers are applied on this thread, so on_result needs no locking. If the
        first few requests all fail, the server is down: stop instead of waiting
        for every remaining request to time out.
        """
        if not tasks:
            return
        succeeded, errors = 0, []
        with ThreadPoolExecutor(max_workers=max(1, self.settings.llm_concurrency), thread_name_prefix="llm") as pool:
            futures = {pool.submit(fn): name for name, fn in tasks}
            for done, future in enumerate(as_completed(futures), start=1):
                name = futures[future]
                try:
                    on_result(name, future.result())
                    succeeded += 1
                except LLMError as exc:
                    log.warning("AI request for %s failed: %s", name, exc)
                    errors.append(exc)
                    failures.append(name)
                    if not succeeded and len(errors) >= 3:
                        for pending in futures:
                            pending.cancel()
                        raise exc
                on_progress(done, len(tasks))
        if errors and not succeeded:
            raise errors[0]

    def _pick_files_to_summarize(self, result: AnalysisResult) -> list[str]:
        in_workflows = {n.file for w in result.workflows for n in w.nodes if n.file}
        ranked = sorted(
            (f for f in result.files if f.parsed),
            key=lambda f: (f.path not in in_workflows, -len(f.imported_by), f.path),
        )
        return [f.path for f in ranked[: self.settings.max_file_summaries]]

    def _summarize_files(self, result: AnalysisResult, index: CodeIndex, report: Reporter, failures: list[str]) -> None:
        by_path = {f.path: f for f in result.files}
        todo: list[str] = []
        for path in self._pick_files_to_summarize(result):
            cached = self.cache.get(self._summary_key(index, path))
            if cached:
                by_path[path].summary = cached
            else:
                todo.append(path)

        size = self.settings.summary_batch_size
        batches = [todo[i:i + size] for i in range(0, len(todo), size)]

        def ask(batch: list[str]) -> Callable[[], tuple[list[str], dict]]:
            payload = [{
                "path": p,
                "functions": by_path[p].functions[:15],
                "imports": (by_path[p].imports + by_path[p].packages)[:12],
                "services": by_path[p].services,
                "docstring": by_path[p].summary,
            } for p in batch]
            messages = [{"role": "system", "content": prompts.SUMMARY_SYSTEM},
                        {"role": "user", "content": json.dumps(payload)}]
            return lambda: (batch, self._chat(messages, prompts.SUMMARY_SCHEMA, "file_summaries",
                                              max_tokens=200 + 80 * len(batch)))

        def apply(_: str, answer: tuple[list[str], dict]) -> None:
            batch, reply = answer
            for item in reply.get("files", []):
                path = item.get("path")
                if path in batch and item.get("summary"):
                    by_path[path].summary = _clip(item["summary"], 200)
                    self.cache.set(self._summary_key(index, path), by_path[path].summary)

        if batches:
            report(f"Summarizing {len(todo)} key files ({self.settings.llm_concurrency} requests at a time)", 0.75)
        self._run_parallel(
            [(f"files {b[0]}", ask(b)) for b in batches], apply,
            lambda done, total: report(f"Summarized {done}/{total} file batches", 0.75 + 0.23 * done / total),
            failures,
        )

    def _summary_key(self, index: CodeIndex, path: str) -> str:
        return Cache.key("summary", prompts.PROMPT_VERSION, self.settings.llm_model, index.files[path].sha)

    @staticmethod
    def _apply_labels(workflow: Graph, reply: dict) -> None:
        nodes = {n.id: n for n in workflow.nodes}
        for step in reply.get("steps", []):
            node = nodes.get(step.get("id"))
            if node is None:
                continue  # the model invented an id; ignore it
            if label := _clip(step.get("label", ""), 52):
                if node.type not in {"trigger", "integration"}:  # keep "POST /refunds" and service names
                    node.label = label
            if summary := _clip(step.get("summary", ""), 240):
                node.summary = summary
        workflow.analysis = _clip(reply.get("analysis", ""), 1500)
        workflow.summary = _clip(reply.get("summary", ""), 500)
        if title := _clip(reply.get("title", ""), 60):
            workflow.title = f"{title} (overview)" if workflow.overview else title

    # ------------------------------------------------------- single file
    def explain_file(self, filename: str, text: str, report: Reporter) -> Graph:
        """Workflow + short summary for one source file in any language."""
        name = PurePosixPath(filename).name
        language = detect_language(name) or PurePosixPath(name).suffix.lstrip(".") or "text"
        lines = text.splitlines()
        numbered, used = [], 0
        for number, line in enumerate(lines, start=1):
            row = f"{number}| {line}"
            if used + len(row) > self.settings.file_max_chars:
                break
            numbered.append(row)
            used += len(row) + 1
        clipped = len(numbered) < len(lines)

        parts = [f"File: {name}", f"Language: {language}"]
        if outline := self._outline(name, text):
            parts.append("Outline (functions, line, calls):\n" + outline)
        if clipped:
            parts.append(f"Note: the file has {len(lines)} lines; only the first {len(numbered)} are shown.")
        parts.append("Code:\n" + "\n".join(numbered))

        key = Cache.key("file", prompts.PROMPT_VERSION, self.settings.llm_model, name, text)
        if (cached := self.cache.get(key)) is not None:
            return Graph.model_validate(cached)

        report(f"AI is reading {name} ({len(lines)} lines)", 0.15)
        messages = [{"role": "system", "content": prompts.FILE_SYSTEM},
                    {"role": "user", "content": "\n\n".join(parts)}]
        graph = self._graph_with_fixes(messages, prompts.FILE_SCHEMA, "file_workflow", report)
        for node in graph.nodes:
            node.file = name
        if clipped:
            graph.warnings.append(f"Only the first {len(numbered)} of {len(lines)} lines were read.")
        self.cache.set(key, graph.model_dump())
        return graph

    @staticmethod
    def _outline(name: str, text: str) -> str:
        """Function list from the parser (Python/JS/TS); helps the model with long files."""
        parsed = parse_file(name, text.encode())
        if not parsed.parsed:
            return ""
        rows = [f"- {fn.qualname} (line {fn.line}) calls: {', '.join(fn.calls[:8]) or '-'}"
                for fn in parsed.functions if not fn.synthetic][:60]
        return "\n".join(rows)

    # -------------------------------------------------------- prompt mode
    def generate_graph(self, prompt: str, report: Reporter) -> Graph:
        messages = [
            {"role": "system", "content": prompts.FLOWCHART_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        return self._graph_with_fixes(messages, prompts.FLOWCHART_SCHEMA, "flowchart", report)

    def _graph_with_fixes(self, messages: list[dict[str, str]], schema: dict, name: str, report: Reporter) -> Graph:
        """Ask for a graph, check it in code, and send only the problems back to the model."""
        graph: Graph | None = None
        for attempt in range(self.settings.llm_fix_retries + 1):
            report("Planning the workflow" if attempt == 0 else f"Fixing problems (attempt {attempt})",
                   0.3 + 0.2 * attempt)
            reply = self._chat(messages, schema, name)
            graph = self._graph_from_reply(reply)
            problems = find_problems(graph)
            if not problems:
                return graph
            messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(reply)},
                {"role": "user", "content": prompts.FIX_INSTRUCTION.format(problems="\n".join(f"- {p}" for p in problems))},
            ]
        assert graph is not None
        repaired = auto_repair(graph)
        repaired.warnings.append("Some parts of the generated graph were repaired automatically.")
        return repaired

    @staticmethod
    def _graph_from_reply(reply: dict) -> Graph:
        nodes = []
        for raw in reply.get("nodes", []):
            node_type = raw.get("type") if raw.get("type") in prompts.NODE_TYPES else "step"
            service = (raw.get("service") or "").strip()
            integration = find_by_name(service) if service else None
            icon = TYPE_ICONS.get(node_type) or (integration.category if integration else
                                                  "tool" if node_type == "tool" else "code")
            line = raw.get("line")
            nodes.append(Node(
                id=str(raw.get("id", "")),
                label=_clip(raw.get("label", ""), 48),
                type=node_type,
                icon=icon,
                line=line if isinstance(line, int) and line > 0 else None,
                services=[integration.label if integration else service] if service else [],
            ))
        edges = [Edge(
            source=str(e.get("source", "")),
            target=str(e.get("target", "")),
            kind=e.get("kind") if e.get("kind") in prompts.EDGE_KINDS else "flow",
            label=_clip(e.get("label", ""), 24),
        ) for e in reply.get("edges", [])]
        mode = reply.get("mode") if reply.get("mode") in {"workflow", "flowchart"} else "flowchart"
        return Graph(mode=mode, title=_clip(reply.get("title", "Generated workflow"), 60),
                     analysis=_clip(reply.get("analysis", ""), 1500),
                     summary=_clip(reply.get("summary", ""), 500), nodes=nodes, edges=edges)
