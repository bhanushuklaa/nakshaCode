"""Convert Mermaid flowcharts into FlowLens training examples (prompt -> flowchart graph).

Supports the common flowchart syntax:

  flowchart TD
    A([Start]) --> B{Is the form valid?}
    B -- Yes --> C[Save to database]
    B -->|No| D[Show error]
    C & D --> E((End))

Examples
--------
  python convert_mermaid.py --input hf:CoreFidelity/Mermaid_50K --out data/mermaid.jsonl
  python convert_mermaid.py --input pairs.jsonl --prompt-field description --code-field mermaid --out data/mermaid.jsonl

Check each dataset's license before you publish a model trained on it.
"""

import argparse
import html
import json
import re

from common import Edge, Graph, Node, clean_graph, clip, find_by_name, flowchart_example, read_records, template_analysis, write_jsonl

PROMPT_FIELDS = ["description", "prompt", "instruction", "question", "input", "query", "text"]
CODE_FIELDS = ["mermaid", "code", "diagram", "flowchart", "output", "response", "completion", "answer"]

HEADER = re.compile(r"^\s*(flowchart|graph)\b", re.I)
SKIP = re.compile(r"^\s*(subgraph\b|end\b|classDef\b|class\s|style\s|linkStyle\b|click\s|%%|direction\s|accTitle|accDescr)", re.I)
ARROW = re.compile(
    r"\s*(?:"
    r"--\s*(?P<l1>[^->|][^>|]*?)\s*-->"           # A -- label --> B
    r"|-\.\s*(?P<l2>[^.>|]+?)\s*\.->"             # A -. label .-> B
    r"|==\s*(?P<l3>[^=>|]+?)\s*==>"               # A == label ==> B
    r"|(?:<-->|-->|---|-\.->|-\.-|==>|===|--o|--x)(?:\s*\|(?P<l4>[^|]*)\|)?"  # A -->|label| B
    r")\s*"
)
# (open, close, shape) pairs, longest first so "([" wins over "(".
SHAPES = [("(((", ")))", "terminal"), ("([", "])", "terminal"), ("((", "))", "terminal"), ("[[", "]]", "box"),
          ("[(", ")]", "database"), ("{{", "}}", "decision"), ("[/", "/]", "box"), ("[\\", "\\]", "box"),
          ("[/", "\\]", "box"), ("[", "]", "box"), ("(", ")", "box"), ("{", "}", "decision"), (">", "]", "box")]
NODE_ID = re.compile(r"^([A-Za-z0-9_\-.]+)(.*)$")
YES = {"yes", "y", "true", "ok", "valid", "approved", "success", "found", "pass", "passed"}
NO = {"no", "n", "false", "invalid", "rejected", "not found", "fail", "failed", "failure"}
END_WORDS = re.compile(r"^(end|done|stop|finish|finished|complete|completed|exit)\b", re.I)


def _split_outside(text: str, sep: str) -> list[str]:
    """Split on `sep`, but not inside [...], (...), {...} or quotes (labels may contain it)."""
    parts, depth, quote, current = [], 0, "", []
    for char in text:
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char in "[({":
            depth += 1
        elif char in "])}":
            depth = max(depth - 1, 0)
        elif char == sep and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _clean_label(text: str) -> str:
    text = html.unescape(text.strip().strip('"').strip("'"))
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return clip(text, 48)


def _parse_node(token: str) -> tuple[str, str, str] | None:
    """'B{Is it valid?}' -> ('B', 'Is it valid?', 'decision')."""
    token = token.strip().rstrip(";")
    match = NODE_ID.match(token)
    if not match:
        return None
    node_id, rest = match.group(1), match.group(2).strip()
    rest = re.sub(r":::\w+$", "", rest).strip()  # class shorthand
    if not rest:
        return node_id, "", "box"
    for open_, close, shape in SHAPES:
        if rest.startswith(open_) and rest.endswith(close) and len(rest) >= len(open_) + len(close):
            return node_id, _clean_label(rest[len(open_):len(rest) - len(close)]), shape
    return node_id, "", "box"


def mermaid_to_graph(code: str, title: str = "") -> Graph | None:
    code = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", code.strip(), flags=re.M)
    lines = [line for raw in code.splitlines() for line in _split_outside(raw, ";") if line.strip()]
    if not lines or not any(HEADER.match(line) for line in lines[:3]):
        return None

    labels: dict[str, str] = {}
    shapes: dict[str, str] = {}
    order: list[str] = []
    edges: list[tuple[str, str, str]] = []

    def register(token: str) -> str | None:
        parsed = _parse_node(token)
        if parsed is None:
            return None
        node_id, label, shape = parsed
        if node_id not in labels:
            order.append(node_id)
            labels[node_id] = label or node_id
            shapes[node_id] = shape
        elif label:  # a later definition gives the label
            labels[node_id], shapes[node_id] = label, shape
        return node_id

    for line in lines:
        if HEADER.match(line) or SKIP.match(line):
            continue
        segments, edge_labels, position = [], [], 0
        for arrow in ARROW.finditer(line):
            segments.append(line[position:arrow.start()])
            edge_labels.append(next((g for g in arrow.groups() if g), "") or "")
            position = arrow.end()
        segments.append(line[position:])
        groups = [[nid for part in _split_outside(seg, "&") if (nid := register(part))] for seg in segments if seg.strip()]
        for i, label in enumerate(edge_labels):
            if i + 1 < len(groups):
                for src in groups[i]:
                    for dst in groups[i + 1]:
                        edges.append((src, dst, _clean_label(label)))

    if len(order) < 2 or not edges:
        return None
    return _build_graph(order, labels, shapes, edges, title)


def _build_graph(order, labels, shapes, edges, title) -> Graph | None:
    incoming = {n: 0 for n in order}
    outgoing = {n: 0 for n in order}
    for src, dst, _ in edges:
        outgoing[src] += 1
        incoming[dst] += 1

    roots = [n for n in order if incoming[n] == 0]
    start = next((n for n in roots if shapes[n] != "decision"), None)
    if start is None:
        return None  # a graph that starts with a question has no clear entry point

    nodes = []
    for node_id in order:
        shape, label = shapes[node_id], labels[node_id]
        if node_id == start:
            node_type = "start"
        elif shape == "decision":
            node_type = "decision"
        elif outgoing[node_id] == 0 and (shape == "terminal" or END_WORDS.match(label)):
            node_type = "end"
        else:
            node_type = "step"
        service = find_by_name(label) if node_type in {"step"} else None
        nodes.append(Node(id=node_id, label=label, type=node_type,
                          services=[service.label] if service else []))

    types = {n.id: n.type for n in nodes}
    graph_edges = []
    for src, dst, label in edges:
        word = label.lower().strip(" .!?")
        if types[src] == "decision" and word in YES:
            graph_edges.append(Edge(source=src, target=dst, kind="yes"))
        elif types[src] == "decision" and word in NO:
            graph_edges.append(Edge(source=src, target=dst, kind="no"))
        else:
            graph_edges.append(Edge(source=src, target=dst, kind="flow", label=label[:24]))
    return Graph(mode="flowchart", title=clip(title or labels[start] or "Flowchart", 60),
                 nodes=nodes, edges=graph_edges)


def extract(record: dict, prompt_field: str | None, code_field: str | None) -> tuple[str, str] | None:
    def looks_like_mermaid(value) -> bool:
        return isinstance(value, str) and bool(re.search(r"^\s*(```mermaid\s*)?(flowchart|graph)\b", value, re.I | re.M))

    code = record.get(code_field) if code_field else next((record[f] for f in CODE_FIELDS if looks_like_mermaid(record.get(f))), None)
    if code is None:
        code = next((v for v in record.values() if looks_like_mermaid(v)), None)
    if not looks_like_mermaid(code):
        return None
    candidates = [prompt_field] if prompt_field else PROMPT_FIELDS
    prompt = next((record[f] for f in candidates if isinstance(record.get(f), str) and record[f].strip()
                   and not looks_like_mermaid(record[f])), "")
    return (clip(prompt, 2000), code) if prompt else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="file (.jsonl/.json/.parquet) or hf:owner/name")
    parser.add_argument("--split", default="train")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt-field")
    parser.add_argument("--code-field")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-nodes", type=int, default=30)
    args = parser.parse_args()

    stats = {"read": 0, "no_mermaid": 0, "invalid": 0, "written": 0}

    def examples():
        for record in read_records(args.input, args.split, args.limit):
            stats["read"] += 1
            found = extract(record, args.prompt_field, args.code_field)
            if found is None:
                stats["no_mermaid"] += 1
                continue
            prompt, code = found
            graph = mermaid_to_graph(code, title=prompt.split(".")[0])
            graph = clean_graph(graph, args.max_nodes) if graph else None
            if graph is None:
                stats["invalid"] += 1
                continue
            graph.analysis = template_analysis(graph)
            stats["written"] += 1
            yield flowchart_example(prompt, graph, source=f"mermaid:{args.input}")

    write_jsonl(args.out, examples())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
