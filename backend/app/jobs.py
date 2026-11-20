"""Background jobs with progress events.

Analysis can take minutes on big repositories, so requests return a job id at
once and the UI follows progress over Server-Sent Events. A small thread pool
caps how many jobs hit the GPU at the same time; the rest wait in the queue.

For multi-server deployments, swap this module for Valkey + RQ; the API
surface (submit / get) stays the same.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)
Status = Literal["queued", "running", "done", "error"]
Reporter = Callable[[str, float], None]


class Progress:
    """Passed to every job: call it to report progress, `.publish()` to show early results.

    publish() lets the UI draw the code-derived workflows right away while the
    AI model is still naming steps; the final result replaces them when done.
    """

    def __init__(self, job: "Job"):
        self._job = job

    def __call__(self, message: str, progress: float) -> None:
        self._job.progress = max(self._job.progress, min(progress, 1.0))
        self._job.events.append({"message": message, "progress": self._job.progress})

    def publish(self, result: dict[str, Any]) -> None:
        self._job.result = result
        self._job.version += 1


JobFn = Callable[[Progress], dict[str, Any]]


class UserFacingError(Exception):
    """An error whose message is safe and useful to show to the user."""


@dataclass
class Job:
    id: str
    kind: str
    status: Status = "queued"
    progress: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None   # partial while running, final when done
    version: int = 0                         # bumped on every publish()
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def public(self, include_result: bool = True) -> dict[str, Any]:
        data = {"id": self.id, "kind": self.kind, "status": self.status,
                "progress": round(self.progress, 3), "error": self.error, "version": self.version}
        if include_result:
            data["result"] = self.result
        return data


class JobManager:
    def __init__(self, max_workers: int, ttl_seconds: int):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def submit(self, kind: str, fn: JobFn) -> Job:
        self._expire_old()
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            queued_ahead = sum(1 for j in self._jobs.values() if j.status in {"queued", "running"}) - 1
        job.events.append({"message": f"Queued ({queued_ahead} jobs ahead)" if queued_ahead else "Starting", "progress": 0.0})
        self._pool.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job, fn: JobFn) -> None:
        job.status = "running"
        report = Progress(job)
        try:
            result = fn(report)
            report.publish(result)
            report("Done", 1.0)
            job.status = "done"
        except UserFacingError as exc:
            job.error, job.status = str(exc), "error"
        except Exception:
            log.exception("Job %s failed", job.id)
            job.error, job.status = "Something went wrong while processing this job.", "error"

    def _expire_old(self) -> None:
        cutoff = time.time() - self._ttl
        with self._lock:
            for job_id in [j.id for j in self._jobs.values() if j.created_at < cutoff and j.status in {"done", "error"}]:
                del self._jobs[job_id]
