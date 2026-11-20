"""Shared helpers for building nakshaCode training data.

Every converter turns some outside format (n8n JSON, Mermaid, ...) into the
 `Graph` schema from backend/app/schema.py, then into a chat example
whose assistant answer is exactly what the backend asks the model for. That
way the fine-tuned model learns the real task, in the real output format.
"""

import json
import random
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.llm import prompts  # noqa: E402
from app.llm.validate import find_problems  # noqa: E402
from app.pipeline.integrations import find_by_name  # noqa: E402
from app.schema import Edge, Graph, Node  # noqa: E402

__all__ = [
    "Edge", "Graph", "Node", "find_by_name", "find_problems", "prompts",
    "clean_graph", "clip", "flowchart_example", "graph_to_target", "humanize_identifier",
    "read_records", "split_train_eval", "template_analysis", "write_jsonl",
]


def clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def humanize_identifier(name: str) -> str:
    """'googleSheetsTrigger' -> 'google sheets trigger'."""
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").replace("-", " ").lower().strip()


# --------------------------------------------------------------------------- #
# Reading and writing records
# --------------------------------------------------------------------------- #

def read_records(source: str, split: str = "train", limit: int | None = None) -> Iterator[dict]:
    """Yield dict records from a .jsonl/.json/.parquet file or a Hugging Face dataset.

    Hugging Face datasets are given as `hf:owner/name` and need `pip install datasets`.
    """
    count = 0
    for record in _iter_source(source, split):
        if isinstance(record, dict):
            yield record
            count += 1
            if limit and count >= limit:
                return


def _iter_source(source: str, split: str) -> Iterable[Any]:
    if source.startswith("hf:"):
        from datasets import load_dataset  # optional dependency
        return load_dataset(source[3:], split=split)
    path = Path(source)
    if path.suffix == ".jsonl":
        return (json.loads(line) for line in path.open(encoding="utf-8") if line.strip())
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else [data]
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq  # optional dependency
        return pq.read_table(path).to_pylist()
    raise ValueError(f"Unsupported input: {source} (use .jsonl, .json, .parquet or hf:owner/name)")


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def split_train_eval(rows: list[dict], eval_ratio: float, seed: int = 42) -> tuple[list[dict], list[dict]]:
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    n_eval = max(1, int(len(rows) * eval_ratio)) if len(rows) > 1 else 0
    return rows[n_eval:], rows[:n_eval]


# --------------------------------------------------------------------------- #
# Graph helpers
# --------------------------------------------------------------------------- #

def clean_graph(graph: Graph, max_nodes: int = 30) -> Graph | None:
    """Drop unconnected nodes, renumber ids n1..nN, and reject invalid graphs."""
    if len(graph.nodes) > 1:
        connected = {e.source for e in graph.edges} | {e.target for e in graph.edges}
        graph.nodes = [n for n in graph.nodes if n.id in connected]
    if not 2 <= len(graph.nodes) <= max_nodes:
        return None

    mapping = {node.id: f"n{i}" for i, node in enumerate(graph.nodes, start=1)}
    for node in graph.nodes:
        node.id = mapping[node.id]
    seen: set[tuple[str, str, str]] = set()
    edges = []
    for edge in graph.edges:
        if edge.source in mapping and edge.target in mapping:
            edge.source, edge.target = mapping[edge.source], mapping[edge.target]
            key = (edge.source, edge.target, edge.kind)
            if key not in seen and edge.source != edge.target:
                seen.add(key)
                edges.append(edge)
    graph.edges = edges
    return graph if not find_problems(graph) else None


def template_analysis(graph: Graph) -> str:
    """A short, factual plan written from the graph itself.

    Real reasoning from a teacher model is better; this keeps the `analysis`
    field present and truthful when no teacher is available.
    """
    by_id = {n.id: n for n in graph.nodes}
    start = next((n for n in graph.nodes if n.type in {"trigger", "start"}), graph.nodes[0])
    steps = [n.label for n in graph.nodes if n.type in {"step", "integration", "agent"}][:6]
    parts = [f"It starts with '{start.label}'."]
    if steps:
        parts.append("Main steps: " + ", ".join(f"'{s}'" for s in steps) + ".")
    for node in graph.nodes:
        if node.type == "decision":
            outs = [f"{e.kind if e.kind in {'yes', 'no'} else (e.label or 'other')} → '{by_id[e.target].label}'"
                    for e in graph.edges if e.source == node.id]
            parts.append(f"Decision '{node.label}': " + "; ".join(outs) + ".")
        elif node.type == "loop":
            parts.append(f"'{node.label}' repeats its body for every item.")
    errors = [e for e in graph.edges if e.kind == "error"]
    if errors:
        parts.append(f"{len(errors)} failure path(s) handle errors.")
    services = sorted({s for n in graph.nodes for s in n.services})
    if services:
        parts.append("Services: " + ", ".join(services) + ".")
    return clip(" ".join(parts), 900)


def graph_to_target(graph: Graph) -> dict:
    """The exact JSON object the backend asks for in prompt mode (FLOWCHART_SCHEMA)."""
    return {
        "analysis": graph.analysis,
        "mode": graph.mode,
        "title": graph.title,
        "nodes": [{"id": n.id, "label": n.label, "type": n.type, "service": n.services[0] if n.services else ""}
                  for n in graph.nodes],
        "edges": [{"source": e.source, "target": e.target, "kind": e.kind, "label": e.label} for e in graph.edges],
    }


def flowchart_example(prompt: str, graph: Graph, source: str) -> dict:
    """One chat-format training example for the prompt -> graph task."""
    return {
        "messages": [
            {"role": "system", "content": prompts.FLOWCHART_SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": json.dumps(graph_to_target(graph), ensure_ascii=False)},
        ],
        "meta": {"task": "flowchart", "source": source},
    }
