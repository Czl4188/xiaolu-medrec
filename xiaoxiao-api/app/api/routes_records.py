from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agent.extractor import extract
from app.agent.refine import refine_extract
from app.api.deps import current_user, rate_limit
from app.db import repo
from app.db.models import Record, User
from app.db.session import get_db
from app.schemas import ExtractIn, ExtractOut, RecordIn, RecordOut, RecordPatch

router = APIRouter(prefix="/api/records", tags=["records"])


def _to_out(record: Record) -> RecordOut:
    return RecordOut(
        id=record.id,
        client_id=record.client_id,
        visit_at=record.visit_at,
        hospital=record.hospital,
        summary=record.summary,
        transcript=record.transcript,
        meds=record.meds or [],
        follow_up=record.follow_up,
        allergy=record.allergy,
        notes=record.notes or [],
        conditions=record.conditions or [],
        discontinued=record.discontinued or [],
        allergy_denied=bool(record.allergy_denied),
        mode=record.mode,
        created_at=record.created_at,
    )


@router.get("", response_model=list[RecordOut])
def list_records(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[RecordOut]:
    return [_to_out(r) for r in repo.list_records(db, user.id)]


@router.post("", response_model=RecordOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("records:write", 120))])
def create_record(
    body: RecordIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecordOut:
    data = body.model_dump()
    data["meds"] = [m if isinstance(m, dict) else m.model_dump() for m in data["meds"]]
    record, created = repo.create_record(db, user.id, data)
    if not created:
        # 幂等：同一条记录重复提交不再新建，直接回已有那条
        return _to_out(record)
    return _to_out(record)


@router.get("/{record_id}", response_model=RecordOut)
def get_record(
    record_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecordOut:
    record = repo.get_record(db, user.id, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")
    return _to_out(record)


@router.patch("/{record_id}", response_model=RecordOut)
def patch_record(
    record_id: str,
    body: RecordPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecordOut:
    """归档后修正：只更新传入的字段，行为 null 表示不改。"""
    record = repo.get_record(db, user.id, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")

    data = body.model_dump(exclude_unset=True)
    if "meds" in data and data["meds"] is not None:
        data["meds"] = [m if isinstance(m, dict) else m.model_dump() for m in data["meds"]]
    for key, value in data.items():
        if value is not None:
            setattr(record, key, value)
    db.commit()
    db.refresh(record)
    return _to_out(record)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(
    record_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    if not repo.delete_record(db, user.id, record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")


@router.post("/extract", response_model=ExtractOut,
             dependencies=[Depends(rate_limit("records:extract", 60))])
async def extract_fields(body: ExtractIn, user: User = Depends(current_user)) -> ExtractOut:
    """把一段转写抽成结构化字段。不落库——用户确认后才 POST /api/records。

    规则先抽一遍（谁、哪几个药），再让模型校对药名与用法；
    模型输出必须能在原文里找到依据，找不到就保留规则值。
    """
    result = extract(body.text)
    result = await refine_extract(body.text, result, user.id)
    return ExtractOut(**result)
