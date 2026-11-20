"""Convert n8n workflow JSON into nakshaCode training examples (prompt -> workflow graph).

Works with any dataset that contains n8n workflow objects ({"nodes": [...],
"connections": {...}}), either as a field, a JSON string, or inside a chat
message. Field names are detected automatically; override with flags.

Examples
--------
  python convert_n8n.py --input hf:Ker102/n8n-workflows-36k --out data/n8n.jsonl
  python convert_n8n.py --input my_workflows.jsonl --prompt-field instruction --out data/n8n.jsonl

Check each dataset's license before you publish a model trained on it.
"""

import argparse
import json
import re
from collections import deque

from common import (
    Edge,
    Graph,
    Node,
    clean_graph,
    clip,
    find_by_name,
    flowchart_example,
    humanize_identifier,
    read_records,
    template_analysis,
    write_jsonl,
)

PROMPT_FIELDS = ["instruction", "prompt", "question", "query", "description", "task", "input", "text"]
GENERIC_NODES = {"set", "code", "function", "functionitem", "merge", "wait", "itemlists", "splitout",
                 "aggregate", "datetime", "html", "markdown", "xml", "crypto", "executeworkflow", "sort", "limit"}
END_NODES = {"respondtowebhook", "noop", "stopanderror"}
DECISION_NODES = {"if", "switch", "filter"}
LOOP_NODES = {"splitinbatches", "loopoveritems"}
AGENT_NODES = {"agent", "chainllm", "chainsummarization", "chainretrievalqa", "openai"}


# --------------------------------------------------------------------------- #
# Finding the workflow and the prompt inside a record
# --------------------------------------------------------------------------- #

def _as_workflow(value) -> dict | None:
    if isinstance(value, dict) and isinstance(value.get("nodes"), list) and isinstance(value.get("connections"), dict):
        return value
    if isinstance(value, str) and '"nodes"' in value:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.M)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return _as_workflow(json.loads(text[start:end + 1]))
            except json.JSONDecodeError:
                return None
    return None


def extract(record: dict, prompt_field: str | None, workflow_field: str | None) -> tuple[str, dict] | None:
    # Chat-style records: {"messages": [{"role": "user"...}, {"role": "assistant"...}]}
    for key in ("messages", "conversations"):
        if isinstance(record.get(key), list):
            turns = record[key]
            user = next((t.get("content") or t.get("value") for t in turns if t.get("role", t.get("from")) in {"user", "human"}), "")
            for turn in reversed(turns):
                workflow = _as_workflow(turn.get("content") or turn.get("value"))
                if workflow:
                    return clip(user, 2000), workflow

    workflow = _as_workflow(record.get(workflow_field)) if workflow_field else None
    if workflow is None:
        workflow = next((w for v in record.values() if (w := _as_workflow(v))), None)
    if workflow is None:
        return None

    candidates = [prompt_field] if prompt_field else PROMPT_FIELDS
    prompt = next((record[f] for f in candidates if isinstance(record.get(f), str) and record[f].strip()
                   and _as_workflow(record[f]) is None), "")
    if not prompt and workflow.get("name"):
        prompt = f"Build an automation workflow: {workflow['name']}"
    return (clip(prompt, 2000), workflow) if prompt else None


# --------------------------------------------------------------------------- #
# n8n -> FlowLens graph
# --------------------------------------------------------------------------- #

def _short_type(node_type: str) -> str:
    return node_type.split(".")[-1].lower()


def _service(node_type: str) -> str:
    short = node_type.split(".")[-1]
    name = humanize_identifier(re.sub(r"(Trigger|Tool|V\d+)$", "", short))
    if name.replace(" ", "") in GENERIC_NODES:
        return ""
    if name.startswith("lm chat ") or name.startswith("lm "):
        name = name.split(" ", 2)[-1]  # "lm chat open ai" -> "open ai"
    found = find_by_name(name) or find_by_name(name.replace(" ", ""))
    return found.label if found else ""


def n8n_to_graph(workflow: dict, title: str = "") -> Graph | None:
    raw_nodes = [n for n in workflow.get("nodes", []) if "stickynote" not in str(n.get("type", "")).lower()]
    if not raw_nodes:
        return None
    ids = {n["name"]: f"n{i}" for i, n in enumerate(raw_nodes, start=1) if "name" in n}
    connections = workflow.get("connections") or {}

    # Sub-nodes (models, tools, memory) connect to an agent via ai_* outputs.
    sub_nodes = {src for src, outs in connections.items() for kind in outs if kind.startswith("ai_")}
    nodes: dict[str, Node] = {}
    for raw in raw_nodes:
        if raw.get("name") not in ids:
            continue
        short = _short_type(str(raw.get("type", "")))
        if "trigger" in short or short in {"webhook", "start", "cron", "interval"}:
            node_type = "trigger"
        elif short in DECISION_NODES:
            node_type = "decision"
        elif short in LOOP_NODES:
            node_type = "loop"
        elif short in END_NODES:
            node_type = "end"
        elif short in AGENT_NODES or "agent" in short:
            node_type = "agent"
        elif raw["name"] in sub_nodes:
            node_type = "tool"
        else:
            node_type = "step"
        service = _service(str(raw.get("type", "")))
        node_id = ids[raw["name"]]
        nodes[node_id] = Node(id=node_id, label=clip(raw["name"], 48), type=node_type,
                              services=[service] if service else [])

    edges: list[Edge] = []
    for src_name, outputs in connections.items():
        src = ids.get(src_name)
        if src is None or src not in nodes:
            continue
        src_raw = next((n for n in raw_nodes if n.get("name") == src_name), {})
        for output_kind, branches in outputs.items():
            for index, targets in enumerate(branches or []):
                for target in targets or []:
                    dst = ids.get(target.get("node"))
                    if dst is None or dst not in nodes:
                        continue
                    if output_kind.startswith("ai_"):
                        # Model sub-nodes become the agent's service; tools become tool nodes.
                        if output_kind == "ai_languageModel":
                            nodes[dst].services = nodes[dst].services or nodes[src].services
                            nodes[src].type = "_drop"
                        else:
                            edges.append(Edge(source=dst, target=src, kind="tool"))
                        continue
                    edges.append(_main_edge(src, dst, index, nodes[src], src_raw))

    keep = {i for i, n in nodes.items() if n.type != "_drop"}
    graph = Graph(mode="workflow", title=clip(title or workflow.get("name") or "Workflow", 60),
                  nodes=[n for i, n in nodes.items() if i in keep],
                  edges=[e for e in edges if e.source in keep and e.target in keep])
    _mark_loop_back_edges(graph)
    return graph


def _main_edge(src: str, dst: str, index: int, node: Node, raw: dict) -> Edge:
    if node.type == "decision":
        short = _short_type(str(raw.get("type", "")))
        if short in {"if", "filter"}:
            return Edge(source=src, target=dst, kind="yes" if index == 0 else "no")
        return Edge(source=src, target=dst, kind="flow", label=f"case {index + 1}")
    if node.type == "loop":  # SplitInBatches v3: output 0 = done, output 1 = loop body
        return Edge(source=src, target=dst, kind="flow", label="done" if index == 0 else "each")
    if index == 1 and raw.get("onError") == "continueErrorOutput":
        return Edge(source=src, target=dst, kind="error")
    return Edge(source=src, target=dst, kind="flow")


def _mark_loop_back_edges(graph: Graph) -> None:
    """Edges that go from a loop's body back into the loop node become kind 'loop'."""
    for loop in (n for n in graph.nodes if n.type == "loop"):
        body = {e.target for e in graph.edges if e.source == loop.id and e.label == "each"}
        reachable, queue = set(body), deque(body)
        while queue:
            current = queue.popleft()
            for edge in graph.edges:
                if edge.source == current and edge.target != loop.id and edge.target not in reachable:
                    reachable.add(edge.target)
                    queue.append(edge.target)
        for edge in graph.edges:
            if edge.target == loop.id and edge.source in reachable:
                edge.kind, edge.label = "loop", ""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="file (.jsonl/.json/.parquet) or hf:owner/name")
    parser.add_argument("--split", default="train")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt-field")
    parser.add_argument("--workflow-field")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-nodes", type=int, default=30)
    args = parser.parse_args()

    stats = {"read": 0, "no_workflow": 0, "invalid": 0, "written": 0}

    def examples():
        for record in read_records(args.input, args.split, args.limit):
            stats["read"] += 1
            found = extract(record, args.prompt_field, args.workflow_field)
            if found is None:
                stats["no_workflow"] += 1
                continue
            prompt, workflow = found
            graph = n8n_to_graph(workflow)
            graph = clean_graph(graph, args.max_nodes) if graph else None
            if graph is None:
                stats["invalid"] += 1
                continue
            graph.analysis = template_analysis(graph)
            stats["written"] += 1
            yield flowchart_example(prompt, graph, source=f"n8n:{args.input}")

    write_jsonl(args.out, examples())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
