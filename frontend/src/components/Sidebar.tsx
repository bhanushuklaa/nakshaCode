import { FileCode2, FolderArchive, LoaderCircle, Search, Sparkles, Star } from "lucide-react";
import { useMemo, useState } from "react";
import { simplifyGraph } from "../lib/simplify";
import { useStore } from "../store";
import type { Graph } from "../types";
import { DropZone } from "./StartPanel";

/** Left panel once there is a result: what was analyzed, its AI summary, and (for zips) the workflow list. */
export function Sidebar() {
  const mode = useStore((s) => s.mode);
  return (
    <aside className="sidebar">
      {mode === "code" && <ProjectSidebar />}
      {mode === "file" && <FileSidebar />}
      {mode === "prompt" && <PromptSidebar />}
      <JobStatus />
    </aside>
  );
}

function ProjectSidebar() {
  const analysis = useStore((s) => s.analysis)!;
  const analyzeZip = useStore((s) => s.analyzeZip);
  const aiPending = useStore((s) => s.job.running && s.job.mode === "code" && s.explaining === null);
  return (
    <>
      <section className="card">
        <div className="card__head">
          <FolderArchive size={18} />
          <div>
            <strong title={analysis.project}>{analysis.project}</strong>
            <span>
              {analysis.stats.files_total} files · {analysis.stats.lines_total.toLocaleString()} lines · {analysis.stats.workflows} workflows
            </span>
          </div>
        </div>
        <AiSummary text={analysis.summary} pending={aiPending} />
        <div className="chips">
          {Object.entries(analysis.stats.languages).slice(0, 6).map(([lang, count]) => (
            <span key={lang} className="chip">{lang} {count}</span>
          ))}
        </div>
      </section>
      <WorkflowList />
      {analysis.warnings.map((w) => <p key={w} className="warning-line">{w}</p>)}
      <DropZone compact accept=".zip" title="Upload another .zip" onFile={(file) => analyzeZip(file)} />
    </>
  );
}

/** Overview pinned on top, then every entry workflow (searchable when there are many). */
function WorkflowList() {
  const workflows = useStore((s) => s.analysis?.workflows ?? []);
  const index = useStore((s) => s.workflowIndex);
  const setWorkflow = useStore((s) => s.setWorkflow);
  const showLogic = useStore((s) => s.showLogic);
  const [query, setQuery] = useState("");

  const items = useMemo(() => workflows.map((w, i) => ({ w, i })), [workflows]);
  const overview = items.find(({ w }) => w.overview);
  const entries = items.filter(({ w }) => !w.overview && matches(w, query));
  const steps = (w: Graph) => Math.max((showLogic ? w : simplifyGraph(w)).nodes.length - 1, 0);

  return (
    <section className="wf-list">
      {overview && (
        <button className={`wf-item wf-item--overview${index === overview.i ? " is-active" : ""}`}
          onClick={() => setWorkflow(overview.i)}>
          <Star size={15} />
          <div>
            <strong>Project overview</strong>
            <span>The whole project, start to end</span>
          </div>
        </button>
      )}
      <h3>Workflows <span>{items.length - (overview ? 1 : 0)}</span></h3>
      {items.length > 9 && (
        <label className="search">
          <Search size={14} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search routes, jobs, files" />
        </label>
      )}
      <div className="wf-list__items">
        {entries.map(({ w, i }) => (
          <button key={i} className={`wf-item${index === i ? " is-active" : ""}`} onClick={() => setWorkflow(i)}>
            <div>
              <strong>{w.title}</strong>
              <span>{steps(w) === 1 ? "1 step" : `${steps(w)} steps`} · {w.nodes[0]?.file ?? ""}</span>
            </div>
            {w.analysis && <Sparkles size={13} className="wf-item__ai" aria-label="Explained by AI" />}
          </button>
        ))}
        {entries.length === 0 && <p className="muted">No workflows match.</p>}
      </div>
    </section>
  );
}

function matches(w: Graph, query: string) {
  const q = query.trim().toLowerCase();
  return !q || w.title.toLowerCase().includes(q) || (w.nodes[0]?.file ?? "").toLowerCase().includes(q)
    || w.nodes[0]?.label.toLowerCase().includes(q);
}

function FileSidebar() {
  const { fileGraph, fileName, analyzeFile } = useStore();
  return (
    <>
      <section className="card">
        <div className="card__head">
          <FileCode2 size={18} />
          <div>
            <strong title={fileName ?? ""}>{fileName}</strong>
            <span>Single file{fileGraph ? ` · ${Math.max(fileGraph.nodes.length - 1, 0)} steps` : ""}</span>
          </div>
        </div>
      </section>
      <DropZone compact title="Explain another file" onFile={(file) => analyzeFile(file)} />
    </>
  );
}

function PromptSidebar() {
  const { promptText, promptGraph } = useStore();
  const setPromptGraph = () => useStore.setState({ promptGraph: null, selectedNodeId: null });
  return (
    <>
      <section className="card">
        <div className="card__head">
          <Sparkles size={18} />
          <div>
            <strong>{promptGraph?.title ?? "Your description"}</strong>
            <span>Generated workflow</span>
          </div>
        </div>
        <p className="card__quote">{promptText}</p>
      </section>
      <button className="btn-outline" onClick={setPromptGraph}>Write a new description</button>
    </>
  );
}

/** AI summary text, or a quiet placeholder while the model is still writing it. */
function AiSummary({ text, pending }: { text: string; pending: boolean }) {
  if (text) return <p className="card__summary">{text}</p>;
  if (!pending) return null;
  return (
    <div className="card__summary card__summary--pending">
      <LoaderCircle size={13} className="spin" /> AI is writing the summary…
    </div>
  );
}

/** Small progress line for work that runs while results are already on screen. */
function JobStatus() {
  const job = useStore((s) => s.job);
  if (!job.running && !job.error) return null;
  return (
    <section className="job-status">
      {job.running && (
        <>
          <div className="progress__bar"><div style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
          <p><LoaderCircle size={12} className="spin" /> {job.messages[job.messages.length - 1] ?? "Working…"}</p>
        </>
      )}
      {job.error && <p className="error">{job.error}</p>}
    </section>
  );
}
