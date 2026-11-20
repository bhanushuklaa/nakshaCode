from fastapi import FastAPI
from .routers import training, inspection
app = FastAPI()
app.include_router(training.router)
app.include_router(inspection.router)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/reports/summary")
def report_summary():
    from .services.rag import answer
    return answer("summarize all inspections")
