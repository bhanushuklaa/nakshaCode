import dagre from "@dagrejs/dagre";
import type { Graph } from "../types";

export const NODE_SIZE = { width: 96, height: 120 };   // icon box + label underneath
export const AGENT_SIZE = { width: 230, height: 84 };
const TOOL_GAP_X = 150;
const TOOL_OFFSET_Y = 190;

export type Positions = Record<string, { x: number; y: number }>;

/** Half the height of each node's visible box, so all box centers line up. */
const BOX_HALF: Record<string, number> = { agent: 42, tool: 32, integration: 32, end: 30 };
const DEFAULT_BOX_HALF = 36;
const LABEL_SPACE = 24; // labels sit under the box, so boxes sit above the dagre center

/**
 * Lay out a workflow left-to-right with dagre, then hang each agent's tools
 * in a row underneath it (the n8n look). Returns top-left positions.
 */
export function layoutGraph(graph: Graph): Positions {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 70, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));

  const toolIds = new Set(graph.edges.filter((e) => e.kind === "tool").map((e) => e.target));
  for (const node of graph.nodes) {
    if (!toolIds.has(node.id)) {
      g.setNode(node.id, node.type === "agent" ? { ...AGENT_SIZE } : { ...NODE_SIZE });
    }
  }
  // Tool edges hang below agents and loop back-edges point backwards, so
  // neither should influence the left-to-right ranking.
  for (const edge of graph.edges) {
    if (edge.kind !== "tool" && edge.kind !== "loop" && g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }
  dagre.layout(g);

  const types = new Map(graph.nodes.map((n) => [n.id, n.type]));
  const positions: Positions = {};
  for (const id of g.nodes()) {
    const { x, y, width } = g.node(id);
    const half = BOX_HALF[types.get(id) ?? ""] ?? DEFAULT_BOX_HALF;
    positions[id] = { x: x - width / 2, y: y - LABEL_SPACE - half };
  }

  // Tools: centered row below their agent.
  const toolsByAgent = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.kind === "tool") {
      toolsByAgent.set(edge.source, [...(toolsByAgent.get(edge.source) ?? []), edge.target]);
    }
  }
  for (const [agentId, tools] of toolsByAgent) {
    const agent = positions[agentId];
    if (!agent) continue;
    const agentWidth = types.get(agentId) === "agent" ? AGENT_SIZE.width : NODE_SIZE.width;
    const center = agent.x + agentWidth / 2;
    const rowWidth = (tools.length - 1) * TOOL_GAP_X;
    tools.forEach((toolId, i) => {
      positions[toolId] = { x: center - rowWidth / 2 + i * TOOL_GAP_X - NODE_SIZE.width / 2, y: agent.y + TOOL_OFFSET_Y };
    });
  }
  return positions;
}
