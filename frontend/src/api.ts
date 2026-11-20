import type { AnalysisResult, Graph, Health, Job } from "./types";

const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const getHealth = () => request<Health>("/api/health");

export function startAnalysis(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{ job_id: string }>("/api/analyze", { method: "POST", body: form });
}

export function startPrompt(prompt: string) {
  return request<{ job_id: string }>("/api/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

/** One source file in any language -> workflow + short summary. */
export function startFile(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{ job_id: string }>("/api/file", { method: "POST", body: form });
}

/** Ask the AI to name the steps of one workflow (on demand). */
export function startExplain(graph: Graph, fileSummaries: Record<string, string>) {
  return request<{ job_id: string }>("/api/explain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, file_summaries: fileSummaries }),
  });
}

/**
 * Follow a job's live progress over Server-Sent Events, then fetch its result.
 * `onPartial` receives early results (e.g. workflows before the AI has named them).
 */
export function followJob<T extends AnalysisResult | Graph>(
  jobId: string,
  onProgress: (message: string, progress: number) => void,
  onPartial?: (result: T) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const events = new EventSource(`${BASE}/api/jobs/${jobId}/events`);
    let finished = false;
    let shownVersion = 0;

    const finish = async () => {
      finished = true;
      events.close();
      try {
        const job = await request<Job<T>>(`/api/jobs/${jobId}`);
        if (job.status === "done" && job.result) resolve(job.result);
        else reject(new Error(job.error ?? "The job failed."));
      } catch (error) {
        reject(error);
      }
    };

    events.addEventListener("progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      onProgress(data.message, data.progress);
    });
    events.addEventListener("partial", async () => {
      if (!onPartial) return;
      try {
        const job = await request<Job<T>>(`/api/jobs/${jobId}`);
        // Ignore stale answers: the final result may already have arrived.
        if (!finished && job.result && job.version > shownVersion) {
          shownVersion = job.version;
          onPartial(job.result);
        }
      } catch {
        /* the final result still arrives through "done" */
      }
    });
    events.addEventListener("done", finish);
    events.addEventListener("error", (event) => {
      // Either our own "error" event (job failed) or a dropped connection.
      if ((event as MessageEvent).data !== undefined || events.readyState === EventSource.CLOSED) {
        finish();
      }
    });
  });
}
