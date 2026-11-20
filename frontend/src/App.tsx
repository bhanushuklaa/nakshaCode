import { FileCode2, FolderArchive, LayoutGrid, Network, Sparkles, Workflow } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { getHealth } from "./api";
import { DetailsPanel } from "./components/DetailsPanel";
import { Sidebar } from "./components/Sidebar";
import { StartPanel } from "./components/StartPanel";
import { useCurrentGraph, useStore, type Mode, type Tab } from "./store";
import { WorkflowView } from "./views/WorkflowView";

// Heavier views load on demand to keep the first page load small.
const FileGraphView = lazy(() => import("./views/FileGraphView").then((m) => ({ default: m.FileGraphView })));
const MapView = lazy(() => import("./views/MapView").then((m) => ({ default: m.MapView })));

const MODES: { id: Mode; label: string; icon: typeof Workflow }[] = [
  { id: "code", label: "Project .zip", icon: FolderArchive },
  { id: "file", label: "Single file", icon: FileCode2 },
  { id: "prompt", label: "Describe", icon: Sparkles },
];

const TABS: { id: Tab; label: string; icon: typeof Workflow }[] = [
  { id: "workflow", label: "Workflow", icon: Workflow },
  { id: "files", label: "File graph", icon: Network },
  { id: "map", label: "Map", icon: LayoutGrid },
];

export default function App() {
  const { mode, setMode, analysis, fileGraph, promptGraph, tab, setTab } = useStore();
  const graph = useCurrentGraph();
  const [llm, setLlm] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    const check = () => getHealth().then((h) => setLlm(h.llm_available ? "online" : "offline")).catch(() => setLlm("offline"));
    check();
    const timer = setInterval(check, 30_000);
    return () => clearInterval(timer);
  }, []);

  const hasResult = mode === "code" ? !!analysis : mode === "file" ? !!fileGraph : !!promptGraph;

  return (
    <div className="app">
      <header className="topbar">
      <div className="brand">
          <span className="brand__logo" aria-hidden>
            <svg viewBox="0 0 32 32" width="26" height="26">
              <rect width="32" height="32" rx="8" fill="#f28c28" />
              <path d="M8 16h5l3-6 3 12 3-6h2" stroke="white" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          CodeNaksha
        </div>
        <nav className="modes" aria-label="What do you want to analyze?">
          {MODES.map(({ id, label, icon: Icon }) => (
            <button key={id} className={mode === id ? "is-active" : ""} onClick={() => setMode(id)}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </nav>
        <span className={`status status--${llm}`} title="AI model status">
          <i /> AI {llm}
        </span>
      </header>

      {hasResult ? (
        <div className="layout">
          <Sidebar />
          <main className="main">
            {mode === "code" && (
              <nav className="tabs">
                {TABS.map(({ id, label, icon: Icon }) => (
                  <button key={id} className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>
                    <Icon size={15} /> {label}
                  </button>
                ))}
              </nav>
            )}
            <div className="view">
              {(mode !== "code" || tab === "workflow") &&
                (graph ? <WorkflowView /> : <p className="view-empty">No entry points were found. Try the File graph or Map.</p>)}
              <Suspense fallback={<p className="view-empty">Loading view…</p>}>
                {mode === "code" && tab === "files" && <FileGraphView />}
                {mode === "code" && tab === "map" && <MapView />}
              </Suspense>
            </div>
          </main>
          <DetailsPanel />
        </div>
      ) : (
        <main className="main main--start">
          <StartPanel llmAvailable={llm === "online"} />
        </main>
      )}
    </div>
  );
}
