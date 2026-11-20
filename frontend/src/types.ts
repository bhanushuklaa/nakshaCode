// Mirrors backend/app/schema.py. Keep both in sync.

export type NodeType =
  | "trigger" | "step" | "agent" | "tool" | "decision" | "loop" | "integration" | "start" | "end";
// flow = next step, yes/no = decision outcomes, tool = agent -> tool,
// error = exception handler path, loop = back-edge from a loop body to its loop node
export type EdgeKind = "flow" | "tool" | "yes" | "no" | "error" | "loop";

// A type alias (not an interface) so React Flow accepts it as node data.
export type GraphNode = {
  id: string;
  label: string;
  type: NodeType;
  icon: string;
  summary: string;
  file: string | null;
  line: number | null;
  function: string | null;
  services: string[];
};

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  label: string;
}

export interface Graph {
  mode: "workflow" | "flowchart";
  title: string;
  analysis: string;
  summary: string;     // short plain-English summary (AI)
  overview: boolean;   // the whole-project overview workflow
  nodes: GraphNode[];
  edges: GraphEdge[];
  warnings: string[];
}

export interface FileEntry {
  path: string;
  language: string;
  lines: number;
  size: number;
  parsed: boolean;
  summary: string;
  functions: string[];
  imports: string[];
  imported_by: string[];
  packages: string[];
  services: string[];
}

export interface AnalysisResult {
  project: string;
  summary: string;
  stats: {
    files_total: number;
    files_parsed: number;
    lines_total: number;
    languages: Record<string, number>;
    skipped: number;
    workflows: number;
  };
  files: FileEntry[];
  workflows: Graph[];
  warnings: string[];
  llm_used: boolean;
}

export interface Job<T> {
  id: string;
  kind: "analyze" | "prompt" | "file" | "explain";
  status: "queued" | "running" | "done" | "error";
  progress: number;
  error: string | null;
  result: T | null;   // partial while running, final when done
  version: number;    // increases each time a new (partial) result is published
}

export interface Health {
  status: string;
  llm_enabled: boolean;
  llm_available: boolean;
}
