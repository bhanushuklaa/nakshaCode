import {
  Background, Controls, MarkerType, ReactFlow, useNodesState, type Edge, type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, LoaderCircle, Sparkles } from "lucide-react";
import { layoutGraph } from "../lib/layout";
import { hasLogic, simplifyGraph } from "../lib/simplify";
import { useCurrentGraph, useStore } from "../store";
import type { EdgeKind } from "../types";
import { FlowNode, type FlowNodeType } from "./FlowNode";

const nodeTypes = { flow: FlowNode };

/** How each edge kind is drawn. Labels are defaults; the backend may send its own. */
const EDGE_STYLES: Record<EdgeKind, { type: string; className: string; label?: string; marker?: string }> = {
  flow: { type: "smoothstep", className: "edge--flow", marker: "#94a3b8" },
  yes: { type: "smoothstep", className: "edge--yes", label: "Yes", marker: "#16a34a" },
  no: { type: "smoothstep", className: "edge--no", label: "No", marker: "#94a3b8" },
  error: { type: "smoothstep", className: "edge--error", label: "Error", marker: "#dc2626" },
  loop: { type: "smoothstep", className: "edge--loop", marker: "#0891b2" },
  tool: { type: "default", className: "edge--tool" },
};

/** The graph as drawn: code workflows are simplified unless "Show conditions & loops" is on. */
function useShownGraph() {
  const graph = useCurrentGraph();
  const mode = useStore((s) => s.mode);
  const showLogic = useStore((s) => s.showLogic);
  return useMemo(
    () => (graph && mode === "code" && !showLogic ? simplifyGraph(graph) : graph),
    [graph, mode, showLogic],
  );
}

export function WorkflowView() {
  const graph = useShownGraph();
  const selectNode = useStore((s) => s.selectNode);

  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const positions = layoutGraph(graph);
    const nodes: FlowNodeType[] = graph.nodes.map((n) => ({
      id: n.id,
      type: "flow",
      position: positions[n.id] ?? { x: 0, y: 0 },
      data: n,
    }));
    const edges: Edge[] = graph.edges.map((e, i) => {
      const style = EDGE_STYLES[e.kind];
      return {
        id: `e${i}`,
        source: e.source,
        target: e.target,
        sourceHandle: e.kind === "tool" ? "tools" : e.kind === "loop" ? "loop-out" : undefined,
        targetHandle: e.kind === "loop" ? "loop-in" : undefined,
        type: style.type,
        label: e.label || style.label,
        animated: false,
        className: style.className,
        markerEnd: style.marker ? { type: MarkerType.ArrowClosed, width: 16, height: 16, color: style.marker } : undefined,
        // Route back-edges in a wide loop under the nodes so they don't hide behind forward edges.
        ...(e.kind === "loop" ? { pathOptions: { offset: 24, borderRadius: 12 } } : {}),
      };
    });
    return { nodes, edges };
  }, [graph]);

  // Local node state so users can drag nodes around; reset when the graph changes.
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<FlowNodeType>(nodes);
  useEffect(() => setFlowNodes(nodes), [nodes, setFlowNodes]);

  const onNodeClick: NodeMouseHandler = (_, node) => selectNode(node.id);

  if (!graph) return <p className="view-empty">No workflow selected.</p>;

  return (
    <div className="workflow-view">
      <WorkflowHeader />
      <ReactFlow
        key={graph.title + graph.nodes.length}
        forceColorMode="light"
        nodes={flowNodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={() => selectNode(null)}
        fitView
        // Leave room at the top for the header card so it doesn't cover the first nodes.
        fitViewOptions={{ padding: { top: headerSpace(graph), left: "40px", right: "40px", bottom: "40px" }, maxZoom: 1.1 }}
        minZoom={0.2}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1.2} color="#d4d4d8" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

function headerSpace(graph: { summary: string; analysis: string; warnings: string[] }) {
  const summaryLines = Math.ceil(graph.summary.length / 85);
  return `${110 + 20 * summaryLines + (graph.analysis ? 22 : 0) + 22 * graph.warnings.length}px` as const;
}

function LogicToggle() {
  const showLogic = useStore((s) => s.showLogic);
  const toggleLogic = useStore((s) => s.toggleLogic);
  return (
    <label className="toggle">
      <input type="checkbox" checked={showLogic} onChange={toggleLogic} />
      <span className="toggle__track"><span className="toggle__thumb" /></span>
      Show conditions &amp; loops
    </label>
  );
}

/** Title, AI summary and actions, floating over the top-left of the canvas. */
function WorkflowHeader() {
  const graph = useCurrentGraph();
  const mode = useStore((s) => s.mode);
  const workflowIndex = useStore((s) => s.workflowIndex);
  const explaining = useStore((s) => s.explaining);
  const explainWorkflow = useStore((s) => s.explainWorkflow);
  const jobRunning = useStore((s) => s.job.running);
  const aiPending = useStore((s) => s.job.running && s.job.mode === "code" && s.explaining === null);
  const [showAnalysis, setShowAnalysis] = useState(false);
  if (!graph) return null;
  const canExplain = mode === "code" && !graph.overview && !graph.analysis && graph.nodes.length > 1;
  const isExplaining = explaining === workflowIndex;
  const summaryPending = graph.overview && !graph.summary && aiPending;

  return (
    <div className="workflow-header">
      <div className="workflow-header__card">
        <h2>{graph.title}</h2>
        {graph.summary && <p className="workflow-header__summary">{graph.summary}</p>}
        {summaryPending && (
          <p className="workflow-header__pending"><LoaderCircle size={13} className="spin" /> AI is writing the project summary…</p>
        )}
        {graph.analysis && (
          <>
            <button className="analysis-card__toggle" onClick={() => setShowAnalysis((v) => !v)}>
              <Sparkles size={13} /> {showAnalysis ? "Hide" : "Show"} how the AI read it
              <ChevronDown size={13} className={showAnalysis ? "rot" : ""} />
            </button>
            {showAnalysis && <p className="workflow-header__analysis">{graph.analysis}</p>}
          </>
        )}
        <div className="header-actions">
          {mode === "code" && hasLogic(graph) && <LogicToggle />}
          {canExplain && (
            <button className="btn-secondary" onClick={() => explainWorkflow(workflowIndex)} disabled={jobRunning}>
              {isExplaining ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}
              {isExplaining ? "AI is explaining…" : "Explain with AI"}
            </button>
          )}
        </div>
      </div>
      {graph.warnings.map((w) => (
        <p key={w} className="warning-line">{w}</p>
      ))}
    </div>
  );
}
