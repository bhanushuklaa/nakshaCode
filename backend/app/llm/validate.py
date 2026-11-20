"""Graph checks done in plain code instead of asking the model to self-reflect.

Cheap and deterministic: only the errors found here are sent back to the
model, which is far cheaper than long "thinking".
"""

from collections import Counter

from ..schema import Graph


def find_problems(graph: Graph) -> list[str]:
    problems: list[str] = []
    ids = [n.id for n in graph.nodes]
    node_ids = set(ids)
    node_types = {n.id: n.type for n in graph.nodes}

    if not graph.nodes:
        return ["The graph has no nodes."]
    for node_id, count in Counter(ids).items():
        if count > 1:
            problems.append(f"Node id '{node_id}' is used {count} times; ids must be unique.")
    for node in graph.nodes:
        if not node.label.strip():
            problems.append(f"Node '{node.id}' has an empty label.")

    connected: set[str] = set()
    outgoing: Counter[str] = Counter()
    for edge in graph.edges:
        if edge.source not in node_ids:
            problems.append(f"Edge source '{edge.source}' does not exist.")
        if edge.target not in node_ids:
            problems.append(f"Edge target '{edge.target}' does not exist.")
        if edge.source == edge.target:
            problems.append(f"Edge from '{edge.source}' to itself is not allowed.")
        connected.update((edge.source, edge.target))
        outgoing[edge.source] += 1

    if len(graph.nodes) > 1:
        for node in graph.nodes:
            if node.id not in connected:
                problems.append(f"Node '{node.id}' is not connected to anything.")
    if not any(n.type in {"start", "trigger"} for n in graph.nodes):
        problems.append("The graph needs one node of type 'start' or 'trigger'.")
    for node in graph.nodes:
        if node.type == "decision" and outgoing[node.id] < 2:
            problems.append(f"Decision '{node.id}' needs at least two outgoing edges (yes/no).")
        if node.type == "loop" and outgoing[node.id] < 1:
            problems.append(f"Loop '{node.id}' needs an edge into its body.")
    for edge in graph.edges:
        if edge.kind == "tool" and node_types.get(edge.source) != "agent":
            problems.append(f"Edge {edge.source}->{edge.target} has kind 'tool' but '{edge.source}' is not an agent.")
    return problems


def auto_repair(graph: Graph) -> Graph:
    """Last resort after retries: drop broken edges and duplicate nodes."""
    seen: set[str] = set()
    nodes = []
    for node in graph.nodes:
        if node.id not in seen and node.label.strip():
            seen.add(node.id)
            nodes.append(node)
    edges = [e for e in graph.edges if e.source in seen and e.target in seen and e.source != e.target]
    return graph.model_copy(update={"nodes": nodes, "edges": edges})
