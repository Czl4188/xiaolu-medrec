"""长任务入口：API 只负责建 run 并投递，不阻塞请求生命周期。

演示任务 `record.reindex`：模拟「把一条记录重新切分/embedding」这类
几十秒级的活。真正做 RAG 时把 worker/tasks.py 里的实现换掉即可，
接口形状不用变。
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import current_user, rate_limit
from app.core.config import settings
from app.db import repo
from app.db.models import User
from app.db.session import get_db
from app.schemas import JobAccepted, RunOut

router = APIRouter(prefix="/api", tags=["ops"])

ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4"}


@router.post("/records/{record_id}/reindex", response_model=JobAccepted,
             dependencies=[Depends(rate_limit("jobs", 30))])
def reindex_record(
    record_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobAccepted:
    record = repo.get_record(db, user.id, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")

    run, _ = repo.create_run(db, user.id, "record.reindex", {"record_id": record_id})
    repo.enqueue(
        db, "record.reindex", {"record_id": record_id, "user_id": user.id, "run_id": run.id},
        idempotency_key=f"reindex:{record_id}:{run.id}",
        max_attempts=settings.job_max_attempts,
    )
    return JobAccepted(run_id=run.id, status=run.status, poll_url=f"/api/runs/{run.id}")


@router.get("/runs/{run_id}", response_model=RunOut)
def read_run(
    run_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RunOut:
    run = repo.get_run(db, user.id, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "运行不存在")
    return RunOut.model_validate(run)


@router.post("/asr", dependencies=[Depends(rate_limit("asr", 20))])
async def transcribe(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """语音转写。

    当前环境没有内置离线 ASR：未配置 ASR_PROVIDER 时明确返回 501，
    而不是假装转写成功、返回一段编出来的文字。
    """
    name = (file.filename or "").lower()
    suffix = name[name.rfind("."):] if "." in name else ""
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"不支持的音频格式：{suffix or '未知'}")

    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                f"文件超过 {settings.max_upload_mb}MB")

    if settings.asr_provider in ("", "none"):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "服务端未配置语音转写（ASR_PROVIDER=none）。可以继续用浏览器自带识别，"
            "或把文字直接 POST 到 /api/records/extract。",
        )

    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        f"ASR provider {settings.asr_provider} 未实现：请在 app/api/routes_ops.py 接入具体厂商 SDK。",
    )
