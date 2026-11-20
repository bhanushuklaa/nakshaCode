import { CircleCheck, FileCode2, FolderArchive, LoaderCircle, Send, Sparkles, Upload } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { useStore, type Mode } from "../store";

const EXAMPLE = "When a customer requests a refund, look up the order in Postgres, let an AI agent check the refund policy and delivery status, then ask a manager for approval on Slack.";

/** Main area before there is a result: one clear card for the selected mode. */
export function StartPanel({ llmAvailable }: { llmAvailable: boolean }) {
  const mode = useStore((s) => s.mode);
  const job = useStore((s) => s.job);
  if (job.running && job.mode === mode) return <ProgressCard />;
  return (
    <div className="start">
      {mode === "code" && <ZipStart />}
      {mode === "file" && <FileStart llmAvailable={llmAvailable} />}
      {mode === "prompt" && <PromptStart llmAvailable={llmAvailable} />}
      {job.error && job.mode === mode && <p className="start__error">{job.error}</p>}
      <p className="start__note">Code is analyzed statically, never executed, and deleted right after analysis.</p>
    </div>
  );
}

function ZipStart() {
  const analyzeZip = useStore((s) => s.analyzeZip);
  return (
    <>
      <StartTitle icon={<FolderArchive size={22} />} title="Analyze a whole project"
        text="Upload a .zip of your repository. You get the full project workflow with an AI summary, every API route and job traced step by step, a file graph and a map." />
      <DropZone accept=".zip" title="Drop your project .zip here" hint="Python, JavaScript and TypeScript · up to 50 MB"
        onFile={(file) => analyzeZip(file)} />
    </>
  );
}

function FileStart({ llmAvailable }: { llmAvailable: boolean }) {
  const analyzeFile = useStore((s) => s.analyzeFile);
  return (
    <>
      <StartTitle icon={<FileCode2 size={22} />} title="Explain a single file"
        text="Upload one source file in any language. The AI reads it and returns a workflow with a short summary, with every step linked to its line." />
      {llmAvailable ? (
        <DropZone title="Drop a code file here" hint="Any language: .py .ts .go .java .cs .php .rb .rs .cpp … · up to 300 KB"
          onFile={(file) => analyzeFile(file)} />
      ) : (
        <p className="start__error">Single-file mode needs the AI model, which is offline right now.</p>
      )}
    </>
  );
}

function PromptStart({ llmAvailable }: { llmAvailable: boolean }) {
  const generate = useStore((s) => s.generateFromPrompt);
  const [prompt, setPrompt] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (prompt.trim().length >= 5) generate(prompt.trim());
  };
  return (
    <>
      <StartTitle icon={<Sparkles size={22} />} title="Describe a workflow"
        text="Write a process in plain words and the AI turns it into a workflow with decisions, agents and services." />
      <form className="start__form" onSubmit={submit}>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} disabled={!llmAvailable}
          placeholder={llmAvailable ? EXAMPLE : "Describe mode needs the AI model, which is offline right now."} />
        <div className="start__actions">
          <button type="button" className="link" onClick={() => setPrompt(EXAMPLE)} disabled={!llmAvailable}>Use example</button>
          <button type="submit" className="btn-primary" disabled={!llmAvailable || prompt.trim().length < 5}>
            <Send size={14} /> Generate workflow
          </button>
        </div>
      </form>
    </>
  );
}

function StartTitle({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return (
    <header className="start__title">
      <span className="start__icon">{icon}</span>
      <h1>{title}</h1>
      <p>{text}</p>
    </header>
  );
}

/** Drag-and-drop file picker. Also used (compact) in the sidebar for "upload another". */
export function DropZone({ title, hint, accept, onFile, compact = false }: {
  title: string; hint?: string; accept?: string; onFile: (file: File) => void; compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const running = useStore((s) => s.job.running);
  const [dragging, setDragging] = useState(false);
  const pick = (file: File | undefined) => { if (file && !running) onFile(file); };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    pick(event.dataTransfer.files[0]);
  };
  return (
    <div
      className={`dropzone${compact ? " dropzone--compact" : ""}${dragging ? " is-dragging" : ""}${running ? " is-disabled" : ""}`}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      role="button"
      tabIndex={0}
    >
      <Upload size={compact ? 15 : 24} />
      <strong>{title}</strong>
      {hint && <span>{hint}</span>}
      <input ref={input} type="file" accept={accept} hidden
        onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
    </div>
  );
}

const STEP_NAMES: Record<Mode, string[]> = {
  code: ["Reading the zip", "Parsing code", "Building workflows", "AI summary"],
  file: ["Uploading", "AI is reading the file", "Building the workflow"],
  prompt: ["Sending", "AI is planning", "Checking the workflow"],
};

/** Progress shown in the middle of the screen until the first result arrives. */
export function ProgressCard() {
  const job = useStore((s) => s.job);
  const names = STEP_NAMES[job.mode ?? "code"];
  const current = Math.min(names.length - 1, Math.floor(job.progress * names.length));
  return (
    <div className="start">
      <div className="progress-card">
        <ol>
          {names.map((name, i) => (
            <li key={name} className={i < current ? "is-done" : i === current ? "is-current" : ""}>
              {i < current ? <CircleCheck size={16} /> : i === current ? <LoaderCircle size={16} className="spin" /> : <i />}
              {name}
            </li>
          ))}
        </ol>
        <div className="progress__bar"><div style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
        <p className="progress-card__message">{job.messages[job.messages.length - 1] ?? "Starting…"}</p>
      </div>
    </div>
  );
}
