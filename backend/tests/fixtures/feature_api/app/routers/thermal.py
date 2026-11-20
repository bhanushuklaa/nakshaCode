from fastapi import APIRouter
from sqlalchemy.orm import Session
from ..db import engine
router = APIRouter(prefix="/training")

def database_session():
    return Session(engine)

@router.post("/thermal")
def train_thermal(payload: dict):
    database_session()
    database_session()
    database_session()
    if payload.get("fast"):
        database_session()
    database_session()
    return {"ok": True}
