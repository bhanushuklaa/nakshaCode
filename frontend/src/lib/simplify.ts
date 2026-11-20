import type { EdgeKind, Graph, GraphEdge, GraphNode, NodeType } from "../types";

/** Node types hidden in the simple view: the "logic" between the real steps. */
const LOGIC_TYPES = new Set<NodeType>(["decision", "loop", "end"]);
const MERGEABLE = new Set<NodeType>(["step", "integration"]);
const REPEAT = / \(x(\d+)\)$/;

export function hasLogic(graph: Graph): boolean {
  return graph.nodes.some((n) => LOGIC_TYPES.has(n.type));
}

/**
 * Simple view of a workflow: remove decisions, loops and end nodes, and
 * reconnect whatever came before each removed node to whatever came after it.
 * Steps, services, agents, tools and error paths stay; ids are unchanged, so
 * selection and the details panel keep working. Repeated steps that end up
 * next to each other are then merged ("Database session (x4)").
 */
export function simplifyGraph(graph: Graph): Graph {
  if (!hasLogic(graph)) return graph;
  let edges: GraphEdge[] = graph.edges.filter((e) => e.kind !== "loop");

  for (const node of graph.nodes) {
    if (!LOGIC_TYPES.has(node.type)) continue;
    const incoming = edges.filter((e) => e.target === node.id);
    const outgoing = edges.filter((e) => e.source === node.id);
    const bridged = incoming.flatMap((inEdge) =>
      outgoing.map((outEdge) => {
        const isError = inEdge.kind === "error" || outEdge.kind === "error";
        const kind: EdgeKind = isError ? "error" : "flow";
        return { source: inEdge.source, target: outEdge.target, kind, label: isError ? inEdge.label : "" };
      }),
    );
    edges = [...edges.filter((e) => e.source !== node.id && e.target !== node.id), ...bridged];
  }

  const seen = new Set<string>();
  const unique = edges.filter((e) => {
    const key = `${e.source}>${e.target}>${e.kind}`;
    if (e.source === e.target || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return mergeRepeats({ ...graph, nodes: graph.nodes.filter((n) => !LOGIC_TYPES.has(n.type)), edges: unique });
}

function sameStep(a: GraphNode, b: GraphNode): boolean {
  if (a.function && b.function) return a.function === b.function && a.file === b.file;
  const base = (n: GraphNode) => n.label.replace(REPEAT, "");
  return base(a) === base(b) && a.services.join() === b.services.join();
}

const countOf = (n: GraphNode) => Number(REPEAT.exec(n.label)?.[1] ?? 1);

/** Fold a straight run of the same step into one node (same rule as the backend). */
function mergeRepeats(graph: Graph): Graph {
  let nodes = graph.nodes.map((n) => ({ ...n }));
  let edges = graph.edges;
  for (let changed = true; changed; ) {
    changed = false;
    for (const edge of edges) {
      const first = nodes.find((n) => n.id === edge.source);
      const second = nodes.find((n) => n.id === edge.target);
      if (
        edge.kind !== "flow" || !first || !second ||
        !MERGEABLE.has(first.type) || !MERGEABLE.has(second.type) ||
        edges.filter((e) => e.source === first.id).length !== 1 ||
        edges.filter((e) => e.target === second.id).length !== 1 ||
        !sameStep(first, second)
      ) continue;
      first.label = `${first.label.replace(REPEAT, "")} (x${countOf(first) + countOf(second)})`;
      nodes = nodes.filter((n) => n.id !== second.id);
      edges = edges.filter((e) => e !== edge).map((e) => (e.source === second.id ? { ...e, source: first.id } : e));
      changed = true;
      break;
    }
  }
  return { ...graph, nodes, edges };
}
