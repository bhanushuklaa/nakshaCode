import { create } from "zustand";
import { followJob, startAnalysis, startExplain, startFile, startPrompt } from "./api";
import type { AnalysisResult, Graph } from "./types";

export type Tab = "workflow" | "files" | "map";
/** The three ways in: a project zip, one code file, or a written description. */
export type Mode = "code" | "file" | "prompt";

interface JobState {
  running: boolean;
  mode: Mode | null;       // which mode started the job
  progress: number;
  messages: string[];
  error: string | null;
}

interface State {
  mode: Mode;
  analysis: AnalysisResult | null;   // from a zip
  fileGraph: Graph | null;           // from a single file
  fileName: string | null;
  promptGraph: Graph | null;         // from a description
  promptText: string;
  tab: Tab;
  workflowIndex: number;
  selectedNodeId: string | null;
  selectedFile: string | null;
  job: JobState;
  showLogic: boolean;                // show decisions, loops and exits in code workflows
  explaining: number | null;         // workflow index the AI is explaining right now

  setMode: (mode: Mode) => void;
  analyzeZip: (file: File) => Promise<void>;
  analyzeFile: (file: File) => Promise<void>;
  generateFromPrompt: (prompt: string) => Promise<void>;
  explainWorkflow: (index: number) => Promise<void>;
  setTab: (tab: Tab) => void;
  setWorkflow: (index: number) => void;
  selectNode: (id: string | null) => void;
  selectFile: (path: string | null) => void;
  toggleLogic: () => void;
}

const idleJob: JobState = { running: false, mode: null, progress: 0, messages: [], error: null };
const LOGIC_KEY = "flowlens.showLogic";

function readShowLogic(): boolean {
  try {
    return localStorage.getItem(LOGIC_KEY) === "true";
  } catch {
    return false; // storage can be unavailable (private mode, sandboxed iframes)
  }
}

export const useStore = create<State>((set, get) => {
  /** Shared runner: start a job, stream progress into the store, return the result. */
  async function run<T extends AnalysisResult | Graph>(
    mode: Mode,
    start: () => Promise<{ job_id: string }>,
    onPartial?: (result: T) => void,
  ) {
    set({ job: { running: true, mode, progress: 0, messages: [], error: null } });
    try {
      const { job_id } = await start();
      const result = await followJob<T>(
        job_id,
        (message, progress) =>
          set((s) => ({ job: { ...s.job, progress, messages: [...s.job.messages, message].slice(-6) } })),
        onPartial,
      );
      set((s) => ({ job: { ...s.job, running: false } }));
      return result;
    } catch (error) {
      set((s) => ({ job: { ...s.job, running: false, error: (error as Error).message } }));
      return null;
    }
  }

  return {
    mode: "code",
    analysis: null,
    fileGraph: null,
    fileName: null,
    promptGraph: null,
    promptText: "",
    tab: "workflow",
    workflowIndex: 0,
    selectedNodeId: null,
    selectedFile: null,
    job: idleJob,
    showLogic: readShowLogic(),
    explaining: null,

    setMode: (mode) =>
      set((s) => ({ mode, selectedNodeId: null, tab: mode === "code" ? s.tab : "workflow" })),

    analyzeZip: async (file) => {
      // The first result (code-derived, right after reading the zip) resets the view;
      // later ones (AI summary and names) update it in place, keeping the user's place.
      let shown = false;
      const show = (result: AnalysisResult) => {
        set((s) =>
          shown
            ? { analysis: result, workflowIndex: Math.min(s.workflowIndex, Math.max(result.workflows.length - 1, 0)) }
            : {
                analysis: result, workflowIndex: 0, selectedNodeId: null, selectedFile: null,
                tab: result.workflows.length ? "workflow" : "files",
              },
        );
        shown = true;
      };
      set({ mode: "code", analysis: null });
      const result = await run<AnalysisResult>("code", () => startAnalysis(file), show);
      if (result) show(result);
    },

    analyzeFile: async (file) => {
      set({ mode: "file", fileGraph: null, fileName: file.name });
      const result = await run<Graph>("file", () => startFile(file));
      if (result) set({ fileGraph: result, selectedNodeId: null });
    },

    generateFromPrompt: async (prompt) => {
      set({ mode: "prompt", promptGraph: null, promptText: prompt });
      const result = await run<Graph>("prompt", () => startPrompt(prompt));
      if (result) set({ promptGraph: result, selectedNodeId: null });
    },

    explainWorkflow: async (index) => {
      const analysis = get().analysis;
      const graph = analysis?.workflows[index];
      if (!analysis || !graph || get().job.running) return;
      // Send only the summaries of files this workflow touches.
      const used = new Set(graph.nodes.map((n) => n.file).filter(Boolean));
      const summaries = Object.fromEntries(
        analysis.files.filter((f) => used.has(f.path) && f.summary).map((f) => [f.path, f.summary]),
      );
      set({ explaining: index });
      const result = await run<Graph>("code", () => startExplain(graph, summaries));
      set((s) => {
        // Ignore the answer if another zip was uploaded in the meantime.
        if (!result || s.analysis !== analysis) return { explaining: null };
        const workflows = analysis.workflows.map((w, i) => (i === index ? result : w));
        return { analysis: { ...analysis, workflows }, explaining: null };
      });
    },

    setTab: (tab) => set({ tab }),
    setWorkflow: (index) => set({ workflowIndex: index, selectedNodeId: null, tab: "workflow" }),
    selectNode: (id) => {
      const node = currentGraph(get())?.nodes.find((n) => n.id === id);
      set({ selectedNodeId: id, selectedFile: node?.file ?? get().selectedFile });
    },
    selectFile: (path) => set({ selectedFile: path, selectedNodeId: null }),
    toggleLogic: () => {
      const showLogic = !get().showLogic;
      try {
        localStorage.setItem(LOGIC_KEY, String(showLogic));
      } catch {
        /* not persisted; fine */
      }
      set({ showLogic, selectedNodeId: null });
    },
  };
});

/** The graph currently shown in the workflow tab. */
export function currentGraph(
  state: Pick<State, "mode" | "promptGraph" | "fileGraph" | "analysis" | "workflowIndex">,
) {
  if (state.mode === "prompt") return state.promptGraph;
  if (state.mode === "file") return state.fileGraph;
  return state.analysis?.workflows[state.workflowIndex] ?? null;
}

export const useCurrentGraph = () => useStore(currentGraph);
