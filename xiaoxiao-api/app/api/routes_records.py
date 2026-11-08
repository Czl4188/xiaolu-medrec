from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from datetime import UTC, datetime

from app.agent import engine
from app.agent import kb as drug_kb
from app.agent.extractor import extract
from app.agent.refine import refine_extract
from app.api.deps import current_user, rate_limit
from app.db import repo
from app.db.models import Record, User
from app.db.session import get_db
from app.schemas import (
    DeletedRecordOut,
    ExtractIn,
    ExtractOut,
    RecordIn,
    RecordOut,
    RecordPatch,
)

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
        allergens=record.allergens or [],
        reading=record.reading or "",
        self_check=record.self_check or [],
        confidence=float(record.confidence or 0),
        uncertain=record.uncertain or [],
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


@router.get("/deleted", response_model=list[DeletedRecordOut])
def list_deleted(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DeletedRecordOut]:
    """最近删除的档案（最多 5 份）。必须在 /{record_id} 之前声明，否则会被当成 id。"""
    return [
        DeletedRecordOut(
            id=r.id,
            visit_at=r.visit_at,
            hospital=r.hospital,
            summary=r.summary,
            med_count=len(r.meds or []),
            deleted_at=r.deleted_at,
        )
        for r in repo.list_deleted_records(db, user.id)
        if r.deleted_at is not None
    ]


@router.post("/{record_id}/restore", response_model=RecordOut)
def restore_record(
    record_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecordOut:
    """把一份删除过的档案恢复到在用列表。"""
    record = repo.restore_record(db, user.id, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份记录不在可恢复范围内")
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
    """软删除：进「最近删除」，可在 5 份之内恢复；超出才真删最旧的那条。"""
    if not repo.delete_record(db, user.id, record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")


def _engine_visit(engine_result: dict, rule: dict) -> tuple[datetime, datetime | None]:
    """理解引擎只认明确日期；认出来就用，否则沿用规则结果。"""
    raw = str(engine_result.get("visit_date") or "").strip()
    if raw:
        try:
            day = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            day = None
        if day is not None:
            tz = datetime.now(UTC).astimezone().tzinfo
            picked = day.replace(hour=9, minute=0, tzinfo=tz)
            return picked, picked
    return rule.get("visit_at") or datetime.now(UTC), rule.get("visit_hint")


def _merge_engine(rule: dict, eng: dict) -> dict:
    """模型抽到的字段优先；空字段回填规则值，保证「宁可少抽」不等于「全都丢」。"""
    out = dict(rule)
    for key in ("hospital", "summary", "follow_up", "notes", "conditions",
                "discontinued", "holes"):
        value = eng.get(key)
        if value:
            out[key] = value
    if not eng.get("meds"):
        out["meds"] = rule.get("meds") or []
    else:
        out["meds"] = eng["meds"]

    # 停用名单常常只有一侧给出：模型只抽到药、规则认出「先别吃了」时，
    # 同一味药会同时出现在用药和停用药里（线上实测「二甲双胍先别吃了」就是）。
    # 合并后统一摘掉，以停用为准。
    stop = {drug_kb.normalize(n) for n in (out.get("discontinued") or []) if str(n).strip()}
    if stop:
        out["meds"] = [m for m in (out.get("meds") or [])
                       if drug_kb.normalize(m.get("name", "")) not in stop]

    # 过敏：模型明确表态（阳性/否认）时以模型为准，完全没提才用规则结果
    if eng.get("allergens"):
        out["allergy"] = eng.get("allergy", "")
        out["allergy_denied"] = bool(eng.get("allergy_denied"))
    out["allergens"] = eng.get("allergens") or []

    out["visit_at"], out["visit_hint"] = _engine_visit(eng, rule)
    out["reading"] = eng.get("reading", "")
    out["self_check"] = eng.get("self_check") or []
    out["confidence"] = eng.get("confidence") or 0.0
    out["uncertain"] = []

    # 合并后重算 holes，避免模型按自己的空值报缺、而规则其实抽到了
    holes = []
    if not out.get("hospital"):
        holes.append("医院")
    if not out.get("summary"):
        holes.append("诊断")
    if not out.get("meds"):
        holes.append("用药")
    if not out.get("follow_up"):
        holes.append("复诊")
    out["holes"] = holes
    return out


@router.post("/extract", response_model=ExtractOut,
             dependencies=[Depends(rate_limit("records:extract", 60))])
async def extract_fields(body: ExtractIn, user: User = Depends(current_user)) -> ExtractOut:
    """把一段转写抽成结构化字段。不落库——用户确认后才 POST /api/records。

    链路：规则先兜底（保证永远有结果）→ 理解引擎 TASK=summarize
    （先 reading 数药、再抽 record、最后 self_check）→ 合并。
    模型失败、JSON 不合法、药名没过守门，都退回规则 + 药名校对。
    """
    rule = extract(body.text)
    eng = await engine.summarize(body.text, user.id) if engine.is_ready() else None

    useful = bool(eng) and any(eng.get(k) for k in ("meds", "hospital", "summary", "follow_up"))
    if useful:
        result = _merge_engine(rule, eng)
        result["source"] = "engine"
    else:
        result = await refine_extract(body.text, rule, user.id)
        result["source"] = "rule"
        result.setdefault("allergens", [])
        result.setdefault("reading", "")
        result.setdefault("self_check", [])
        result.setdefault("confidence", 0.0)
        result.setdefault("uncertain", [])
    return ExtractOut(**result)
