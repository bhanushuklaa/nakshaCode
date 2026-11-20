import { FileText, X } from "lucide-react";
import type { ReactNode } from "react";
import { iconFor } from "../lib/icons";
import { useCurrentGraph, useStore } from "../store";

/** Right-hand panel: details for the selected workflow step or file. */
export function DetailsPanel() {
  const graph = useCurrentGraph();
  const { analysis, mode, tab, selectedNodeId, selectedFile, selectFile, selectNode, setTab, setWorkflow } = useStore();
  const node = tab === "workflow" ? graph?.nodes.find((n) => n.id === selectedNodeId) : undefined;
  const file = mode === "code" ? analysis?.files.find((f) => f.path === selectedFile) : undefined;

  if (!node && !file) return null;
  const close = () => { selectNode(null); selectFile(null); };

  if (node) {
    const { icon: Icon, color } = iconFor(node.icon);
    return (
      <aside className="details">
        <header>
          <span className="details__icon" style={{ color }}><Icon size={18} /></span>
          <h3>{node.label}</h3>
          <button className="icon-btn" onClick={close} aria-label="Close"><X size={16} /></button>
        </header>
        <Badge>{node.type}</Badge>
        {node.summary && <p className="details__summary">{node.summary}</p>}
        {node.services.length > 0 && <Field label="Services">{node.services.join(", ")}</Field>}
        {node.function && <Field label="Function"><code>{node.function}</code></Field>}
        {node.file && (
          <Field label="File">
            {mode === "code" ? (
              <button className="link" onClick={() => selectFile(node.file)}>
                {node.file}{node.line ? `:${node.line}` : ""}
              </button>
            ) : (
              <code>{node.file}{node.line ? `:${node.line}` : ""}</code>
            )}
          </Field>
        )}
      </aside>
    );
  }

  const workflows = (analysis?.workflows ?? [])
    .map((w, index) => ({ w, index }))
    .filter(({ w }) => w.nodes.some((n) => n.file === file!.path));

  return (
    <aside className="details">
      <header>
        <span className="details__icon"><FileText size={18} /></span>
        <h3 title={file!.path}>{file!.path.split("/").pop()}</h3>
        <button className="icon-btn" onClick={close} aria-label="Close"><X size={16} /></button>
      </header>
      <p className="details__path">{file!.path}</p>
      <Badge>{file!.language}</Badge> <Badge>{file!.lines} lines</Badge>
      {file!.summary && <p className="details__summary">{file!.summary}</p>}
      {file!.services.length > 0 && <Field label="Services">{file!.services.join(", ")}</Field>}

      {workflows.length > 0 && (
        <Field label="Used in workflows">
          {workflows.map(({ w, index }) => (
            <button key={index} className="link block" onClick={() => { setWorkflow(index); setTab("workflow"); }}>
              {w.title}
            </button>
          ))}
        </Field>
      )}
      <FileList label="Imports" paths={file!.imports} onPick={selectFile} />
      <FileList label="Imported by" paths={file!.imported_by} onPick={selectFile} />
      {file!.packages.length > 0 && <Field label="Packages">{file!.packages.join(", ")}</Field>}
      {file!.functions.length > 0 && (
        <Field label={`Functions (${file!.functions.length})`}>
          <div className="code-list">{file!.functions.slice(0, 30).map((f) => <code key={f}>{f}</code>)}</div>
        </Field>
      )}
    </aside>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <span className="field__label">{label}</span>
      <div>{children}</div>
    </div>
  );
}

function Badge({ children }: { children: ReactNode }) {
  return <span className="badge">{children}</span>;
}

function FileList({ label, paths, onPick }: { label: string; paths: string[]; onPick: (p: string) => void }) {
  if (paths.length === 0) return null;
  return (
    <Field label={`${label} (${paths.length})`}>
      {paths.slice(0, 25).map((p) => (
        <button key={p} className="link block" onClick={() => onPick(p)}>{p}</button>
      ))}
    </Field>
  );
}
