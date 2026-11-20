from fastapi import APIRouter, Depends
from ..db import get_db
from ..services.vision import detect_defects
router = APIRouter(prefix="/training")

@router.post("/rgb")
def train_rgb(payload: dict, db=Depends(get_db)):
    job = db.query(Job).filter(Job.id == payload["id"]).first()
    if not job:
        raise HTTPException(404)
    db.add(job)
    db.commit()
    db.refresh(job)
    for image in payload["images"]:
        boxes = detect_defects(image)
        db.add(Result(boxes=boxes))
    db.commit()
    db.refresh(job)
    return job

@router.get("/status/{job_id}")
def status(job_id: int, db=Depends(get_db)):
    return db.query(Job).get(job_id)
