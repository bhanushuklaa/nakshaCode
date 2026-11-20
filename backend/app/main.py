"""nakshaCode API.

Endpoints
---------
GET  /api/health            server + model status
POST /api/analyze           upload a .zip, returns {job_id}
POST /api/prompt            describe a workflow in words, returns {job_id}
POST /api/file              upload one source file (any language), returns {job_id}
POST /api/explain           AI names for one workflow (on demand), returns {job_id}
GET  /api/jobs/{id}         job status and result
GET  /api/jobs/{id}/events  live progress (Server-Sent Events)
"""

import asyncio
import json
import logging
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import Settings, get_settings
from .jobs import JobManager, Progress, UserFacingError
from .llm.cache import Cache
from .llm.client import LLMClient, LLMError
from .llm.enrich import Enricher
from .pipeline.analyzer import analyze_repository
from .pipeline.ingest import IngestError, extract_zip
from .schema import ExplainRequest, PromptRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = LLMClient(settings)
    app.state.settings = settings
    app.state.llm = client
    app.state.enricher = Enricher(client, Cache(settings.data_dir / "cache.sqlite3"), settings)
    app.state.jobs = JobManager(settings.max_concurrent_jobs, settings.job_ttl_minutes * 60)
    yield
    app.state.jobs.shutdown()


app = FastAPI(title="FlowLens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- helpers
class RateLimiter:
    """Sliding-window limit per client IP (in memory, good for one server)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def __call__(self, request: Request) -> None:
        settings: Settings = request.app.state.settings
        if settings.rate_limit_per_hour <= 0:
            return
        ip = request.client.host if request.client else "unknown"
        if settings.trust_proxy_headers:
            ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", ip).split(",")[0].strip()
        now, window = time.time(), self._hits[ip]
        while window and window[0] < now - 3600:
            window.popleft()
        if len(window) >= settings.rate_limit_per_hour:
            raise HTTPException(429, "Hourly limit reached. Please try again later.")
        window.append(now)


rate_limit = RateLimiter()


async def _save_upload(upload: UploadFile, settings: Settings) -> Path:
    """Stream the upload to disk, enforcing the size limit while reading."""
    limit = settings.max_upload_mb * CHUNK
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=settings.data_dir)
    size = 0
    with handle:
        while chunk := await upload.read(CHUNK):
            size += len(chunk)
            if size > limit:
                handle.close()
                Path(handle.name).unlink(missing_ok=True)
                raise HTTPException(413, f"Zip is larger than {settings.max_upload_mb} MB.")
            handle.write(chunk)
    return Path(handle.name)


# ----------------------------------------------------------------- routes
@app.get("/api/health")
def health(request: Request) -> dict:
    client: LLMClient = request.app.state.llm
    return {"status": "ok", "llm_enabled": client.enabled, "llm_available": client.is_available()}


@app.post("/api/analyze", dependencies=[Depends(rate_limit)])
async def analyze(request: Request, file: UploadFile) -> dict:
    settings: Settings = request.app.state.settings
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Please upload a .zip file.")
    zip_path = await _save_upload(file, settings)
    project = PurePosixPath(file.filename or "project.zip").stem
    client: LLMClient = request.app.state.llm
    enricher: Enricher = request.app.state.enricher

    def run(report: Progress) -> dict:
        try:
            # Extract in the system temp folder, never inside the project:
            # `uvicorn --reload` would see the uploaded .py files and restart mid-job.
            with tempfile.TemporaryDirectory(prefix="flowlens-") as workdir:
                report("Extracting archive", 0.02)
                ingested = extract_zip(
                    zip_path, Path(workdir),
                    max_files=settings.max_files,
                    max_uncompressed_bytes=settings.max_uncompressed_mb * CHUNK,
                    max_file_bytes=settings.max_file_kb * 1024,
                )
                result, index = analyze_repository(ingested, project, report)
            # Source files are deleted at this point; only the analysis remains.
            if client.is_available():
                report.publish(result.model_dump())  # show code-derived workflows now
                enricher.enrich_analysis(result, index, report, publish=lambda: report.publish(result.model_dump()))
            else:
                result.warnings.append("AI model is offline. Names come straight from the code.")
            return result.model_dump()
        except IngestError as exc:
            raise UserFacingError(str(exc)) from exc
        finally:
            zip_path.unlink(missing_ok=True)

    job = request.app.state.jobs.submit("analyze", run)
    return {"job_id": job.id}


@app.post("/api/prompt", dependencies=[Depends(rate_limit)])
def prompt(request: Request, body: PromptRequest) -> dict:
    client: LLMClient = request.app.state.llm
    if not client.is_available():
        raise HTTPException(503, "The AI model is offline, so prompt mode is unavailable.")
    enricher: Enricher = request.app.state.enricher

    def run(report: Progress) -> dict:
        try:
            return enricher.generate_graph(body.prompt, report).model_dump()
        except LLMError as exc:
            raise UserFacingError(f"The model could not build this workflow: {exc}") from exc

    job = request.app.state.jobs.submit("prompt", run)
    return {"job_id": job.id}


@app.post("/api/file", dependencies=[Depends(rate_limit)])
async def explain_file(request: Request, file: UploadFile) -> dict:
    """One source file in any language -> workflow + short summary."""
    settings: Settings = request.app.state.settings
    client: LLMClient = request.app.state.llm
    if not client.is_available():
        raise HTTPException(503, "The AI model is offline, so single-file mode is unavailable.")
    data = await file.read(settings.single_file_max_kb * 1024 + 1)
    if len(data) > settings.single_file_max_kb * 1024:
        raise HTTPException(413, f"File is larger than {settings.single_file_max_kb} KB.")
    if not data.strip():
        raise HTTPException(400, "The file is empty.")
    if b"\x00" in data[:8192]:
        raise HTTPException(400, "This looks like a binary file. Please upload a source code file.")
    text = data.decode("utf-8", errors="replace")
    filename = PurePosixPath(file.filename or "file.txt").name
    enricher: Enricher = request.app.state.enricher

    def run(report: Progress) -> dict:
        try:
            return enricher.explain_file(filename, text, report).model_dump()
        except LLMError as exc:
            raise UserFacingError(f"The model could not explain this file: {exc}") from exc

    job = request.app.state.jobs.submit("file", run)
    return {"job_id": job.id}


@app.post("/api/explain")
def explain_workflow(request: Request, body: ExplainRequest) -> dict:
    """AI names one workflow on demand; answers are cached, so repeats are instant."""
    client: LLMClient = request.app.state.llm
    if not client.is_available():
        raise HTTPException(503, "The AI model is offline.")
    enricher: Enricher = request.app.state.enricher

    def run(report: Progress) -> dict:
        try:
            return enricher.explain_workflow(body.graph, body.file_summaries, report).model_dump()
        except LLMError as exc:
            raise UserFacingError(f"The model could not explain this workflow: {exc}") from exc

    job = request.app.state.jobs.submit("explain", run)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found or expired.")
    return job.public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    jobs: JobManager = request.app.state.jobs
    if jobs.get(job_id) is None:
        raise HTTPException(404, "Job not found or expired.")

    async def stream():
        sent, version, idle = 0, 0, 0.0
        while not await request.is_disconnected():
            job = jobs.get(job_id)
            if job is None:
                break
            while sent < len(job.events):
                yield f"event: progress\ndata: {json.dumps(job.events[sent])}\n\n"
                sent += 1
                idle = 0.0
            if job.version > version and job.status == "running":
                version = job.version  # an early result is ready; the UI fetches it
                yield f"event: partial\ndata: {json.dumps({'version': version})}\n\n"
            if job.status in {"done", "error"} and sent >= len(job.events):
                yield f"event: {job.status}\ndata: {json.dumps(job.public(include_result=False))}\n\n"
                break
            await asyncio.sleep(0.3)
            idle += 0.3
            if idle >= 15:  # keep proxies from closing the connection
                yield ": keep-alive\n\n"
                idle = 0.0

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)
