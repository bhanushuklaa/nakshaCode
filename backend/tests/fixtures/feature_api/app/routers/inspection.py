from fastapi import APIRouter, Depends, UploadFile
from ..db import get_db
from ..services.vision import detect_defects
from ..services.rag import answer
import boto3
s3 = boto3.client("s3")
router = APIRouter(prefix="/inspection")

@router.post("/upload")
def upload(file: UploadFile, db=Depends(get_db)):
    s3.upload_fileobj(file.file, "bucket", file.filename)
    defects = detect_defects(file.filename)
    db.add(Inspection(defects=defects))
    db.commit()
    return {"defects": defects}

@router.post("/ask")
def ask(question: str):
    return answer(question)

@router.get("/list")
def list_all(db=Depends(get_db)):
    return db.query(Inspection).all()
