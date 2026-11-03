"""服务端模型代理。

前端不再要求用户填 API Key：Key 只放在服务端环境变量里，浏览器只调这里。
这样换设备、换浏览器都能直接用，也不会把密钥暴露在页面源码里。
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, rate_limit
from app.core.config import settings
from app.db.models import User

logger = logging.getLogger("app.ai")
router = APIRouter(prefix="/api/ai", tags=["ai"])

MAX_CHARS = 12000
MAX_IMAGE_CHARS = 6_000_000

VISION_SYS = "你是药品包装与处方的识别器。只输出 JSON，不要解释，不要 Markdown 代码块。"
VISION_USER = (
    "识别图片里出现的所有药品，输出 JSON 数组，每项四个字段：\n"
    '[{"name":"药品名称","dose":"单次剂量，如 20mg","freq":"频次，如 每天三次",'
    '"timing":"饭前/饭后/睡前/空腹/随餐"}]\n'
    "规则：看不清、或图片里没有的字段填空串；剂量优先填包装规格（如 0.5g、20mg），"
    "服用粒数/片数不算剂量；不要猜；不要输出诊断或用药建议。"
)


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=12)
    temperature: float = 0.3
    max_tokens: int = 800


class VisionIn(BaseModel):
    image: str


def _enabled() -> bool:
    return bool(settings.llm_api_key) and settings.llm_provider not in ("", "none")


@router.get("/status")
def ai_status(_user: User = Depends(current_user)) -> dict:
    on = _enabled()
    return {
        "enabled": on,
        "text_model": settings.llm_model if on else "",
        "vision_model": settings.llm_vision_model if on else "",
    }


async def _call(
    messages: list[dict],
    model: str,
    *,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    if not _enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务端未配置识别引擎")

    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            )
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "识别服务超时，请重试") from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "识别服务连接失败") from None

    if resp.status_code == 401:
        logger.error("模型鉴权失败，请检查服务端 LLM_API_KEY")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "识别服务鉴权失败")
    if resp.status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "识别服务繁忙，请稍后再试")
    if resp.status_code >= 400:
        logger.warning("模型返回 %s", resp.status_code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "识别服务暂时不可用")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "识别服务返回异常") from None

    if isinstance(content, list):
        content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
    if not content:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "识别服务没有返回内容")
    return str(content)


@router.post("/chat", dependencies=[Depends(rate_limit("ai", 30))])
async def chat(body: ChatIn, _user: User = Depends(current_user)) -> dict:
    if sum(len(m.content) for m in body.messages) > MAX_CHARS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "对话内容过长")
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    text = await _call(
        messages,
        settings.llm_model,
        temperature=max(0.0, min(1.0, body.temperature)),
        max_tokens=max(16, min(1200, body.max_tokens)),
        timeout=settings.llm_timeout_seconds + 10,
    )
    return {"answer": text}


@router.post("/vision", dependencies=[Depends(rate_limit("ai", 12))])
async def vision(body: VisionIn, _user: User = Depends(current_user)) -> dict:
    image = body.image.strip()
    if not image.startswith("data:image/") or ";base64," not in image:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片格式不正确")
    if len(image) > MAX_IMAGE_CHARS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "图片过大，请重新拍摄")

    messages = [
        {"role": "system", "content": VISION_SYS},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_USER},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        },
    ]
    text = await _call(messages, settings.llm_vision_model, temperature=0.1, max_tokens=900, timeout=65)
    return {"text": text}
