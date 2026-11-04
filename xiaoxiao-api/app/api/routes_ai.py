"""服务端模型代理。

前端不再要求用户填 API Key：Key 只放在服务端环境变量里，浏览器只调这里。
这样换设备、换浏览器都能直接用，也不会把密钥暴露在页面源码里。
"""

import asyncio
import base64
import binascii
import logging
import re
import uuid

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
# 音频 base64 上限：约 9MB 原始音频，足够 3 分钟 opus
MAX_AUDIO_CHARS = 12_000_000

ASR_SYS = (
    "你是语音转写器。只把音频里说的话逐字转成文字，不要回答、不要解释、不要总结、不要补充。"
    "听不清的地方留空，不要猜；没有语音就输出空字符串。"
)

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
    extra: dict | None = None,
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
    if extra:
        payload.update(extra)
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


@router.post("/asr", dependencies=[Depends(rate_limit("ai:asr", 20))])
async def asr(body: dict, _user: User = Depends(current_user)) -> dict:
    """语音转写：浏览器录音（webm/opus、mp4、wav 的 data URL）→ 文字。

    走的是能听音频的对话模型，和文本模型共用同一套网关配置；
    只返回转写文本，不生成任何回答。
    """
    audio = str(body.get("audio") or "").strip()
    if not audio.startswith("data:audio/") or ";base64," not in audio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "音频格式不正确")
    if len(audio) > MAX_AUDIO_CHARS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "录音过长，请分段说")

    messages = [
        {"role": "system", "content": ASR_SYS},
        {"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio}},
        ]},
    ]
    text = await _call(
        messages,
        settings.asr_chat_model,
        temperature=0.0,
        max_tokens=1500,
        timeout=90,
        extra={"modalities": ["text"]},
    )
    return {"text": _clean_asr(text)}


def _clean_asr(text: str) -> str:
    """模型偶尔会带「转写：」前缀或引号，这里统一清掉，避免脏文本进档案。"""
    t = str(text or "").strip()
    for prefix in ("转写：", "转写:", "音频内容：", "音频内容:", "文字："):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    if len(t) >= 2 and t[0] in "“\"「'" and t[-1] in "”\"」'":
        t = t[1:-1].strip()
    t = " ".join(t.split())
    return t[:4000]


# ============================================================
# 说话人分离：医生 / 患者
# 走 DashScope 非实时转写（异步任务 + 临时文件），拿到 speaker_id 再判角色。
# ============================================================

_AUDIO_EXT = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/3gpp": ".3gp",
}

DOC_HINTS = (
    "开", "吃", "服", "复查", "复诊", "检查", "化验", "胃镜", "肠镜", "CT", "B超",
    "剂量", "每天", "每日", "饭前", "饭后", "睡前", "空腹", "早晚", "一次", "一片",
    "忌", "别", "注意", "建议", "诊断", "考虑", "住院", "手术", "输液", "打针",
    "报告", "疗程", "停药", "两周", "三天", "一周", "先", "再",
)
PAT_HINTS = (
    "我", "疼", "痛", "难受", "不舒服", "恶心", "吐", "拉", "烧", "痒", "头晕",
    "睡不着", "多久", "最近", "感觉", "胀", "咳", "酸", "麻", "乏力", "没胃口",
)


def _audio_bytes(audio: str) -> tuple[bytes, str]:
    """解析 data URL，返回原始字节与扩展名。"""
    if len(audio) > MAX_AUDIO_CHARS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "录音过长，请分段说")
    m = re.match(r"^data:(audio/[^;,]+)(?:;[^,]*)?;base64,(.+)$", audio, re.S | re.I)
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "音频格式不正确")
    ext = _AUDIO_EXT.get(m.group(1).lower())
    if not ext:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持这种录音格式")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "录音数据不完整") from None
    if len(raw) < 1200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "录音太短")
    if len(raw) > 9 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "录音过长，请分段说")
    return raw, ext


def _dashscope_base() -> str:
    if "dashscope" not in settings.llm_base_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务端未配置说话人分离")
    return settings.asr_diarize_base.rstrip("/")


async def _upload_temp(client: httpx.AsyncClient, raw: bytes, ext: str, model: str) -> str:
    """把音频传到 DashScope 临时存储，换一个 48 小时有效的 oss:// 地址。"""
    base = _dashscope_base()
    auth = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        policy_resp = await client.get(
            f"{base}/api/v1/uploads",
            params={"action": "getPolicy", "model": model},
            headers=auth,
        )
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "说话人分离暂时不可用") from None
    if policy_resp.status_code >= 400:
        logger.warning("获取上传凭证失败 %s", policy_resp.status_code)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "说话人分离暂时不可用")
    policy = policy_resp.json()["data"]

    name = f"{uuid.uuid4().hex}{ext}"
    key = f"{policy['upload_dir']}/{name}"
    files = {
        "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
        "Signature": (None, policy["signature"]),
        "policy": (None, policy["policy"]),
        "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
        "x-oss-forbid-overwrite": (None, policy["x_oss_forbid_overwrite"]),
        "key": (None, key),
        "success_action_status": (None, "200"),
        "file": (name, raw, "application/octet-stream"),
    }
    try:
        up = await client.post(policy["upload_host"], files=files)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "录音上传失败，请重试") from None
    if up.status_code != 200:
        logger.warning("上传录音失败 %s %s", up.status_code, up.text[:200])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "录音上传失败，请重试")
    return f"oss://{key}"


async def _filetrans(raw: bytes, ext: str) -> list[dict]:
    """提交非实时转写任务，轮询完成后下载结果里的句子。"""
    model = settings.asr_diarize_model
    base = _dashscope_base()
    timeout = httpx.Timeout(settings.asr_diarize_timeout, connect=15.0)
    auth = {"Authorization": f"Bearer {settings.llm_api_key}"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        url = await _upload_temp(client, raw, ext, model)
        headers = {
            **auth,
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
            "X-DashScope-OssResourceResolve": "enable",
        }
        payload = {
            "model": model,
            "input": {"file_urls": [url]},
            "parameters": {
                "channel_id": [0],
                "language_hints": ["zh"],
                "diarization_enabled": True,
                "speaker_count": 2,
                "disfluency_removal_enabled": True,
            },
        }
        try:
            sub = await client.post(
                f"{base}/api/v1/services/audio/asr/transcription",
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "说话人分离暂时不可用") from None
        if sub.status_code >= 400:
            logger.warning("提交转写任务失败 %s %s", sub.status_code, sub.text[:300])
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "说话人分离暂时不可用")
        task_id = sub.json()["output"]["task_id"]

        result = None
        for _ in range(45):
            await asyncio.sleep(1.2)
            try:
                q = await client.get(f"{base}/api/v1/tasks/{task_id}", headers=auth)
                data = q.json()
            except (httpx.HTTPError, ValueError):
                continue
            status_ = data.get("output", {}).get("task_status")
            if status_ == "SUCCEEDED":
                result = data["output"]
                break
            if status_ == "FAILED":
                logger.warning("转写任务失败 %s", data.get("output"))
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "这段录音没转写成功，请重试")
        if not result:
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "转写超时，请稍后重试")

        items = result.get("results") or []
        turl = items[0].get("transcription_url") if items else ""
        if not turl:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "转写结果为空")
        try:
            tr = await client.get(turl)
            tr.raise_for_status()
            doc = tr.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "转写结果读取失败") from None

    sentences: list[dict] = []
    for t in doc.get("transcripts") or []:
        for s in t.get("sentences") or []:
            text = str(s.get("text") or "").strip()
            if text:
                sentences.append({
                    "speaker_id": s.get("speaker_id"),
                    "text": text,
                    "begin": int(s.get("begin_time") or 0),
                    "end": int(s.get("end_time") or 0),
                })
    return sentences


def _guess_doctor(sentences: list[dict], ids: list[int]) -> int | None:
    """规则兜底：谁更像在下诊断、开药、嘱咐。"""
    scores: dict[int, int] = {}
    for i in ids:
        txt = "".join(s["text"] for s in sentences if s["speaker_id"] == i)
        scores[i] = sum(1 for w in DOC_HINTS if w in txt) - sum(1 for w in PAT_HINTS if w in txt)
        # 开口就叫「医生」的人，通常不是医生
        if re.search(r"(^|[，。！？\s])医生[，,！!？?]?", txt[:30]):
            scores[i] -= 4
    best = max(scores.values()) if scores else None
    winners = [i for i, v in scores.items() if v == best]
    if best and best > 0 and len(winners) == 1:
        return winners[0]
    return None


async def _ask_doctor(sentences: list[dict], ids: list[int]) -> int | None:
    """让文本模型读一遍，判断哪位是医生。失败就返回 None 交给规则。"""
    groups = []
    for i in ids:
        txt = "".join(s["text"] for s in sentences if s["speaker_id"] == i)
        if len(txt) > 1200:
            txt = txt[:800] + "……" + txt[-300:]
        groups.append(f"speaker {i}：{txt}")
    prompt = (
        "下面是一段门诊问诊录音的转写，按说话人分组。判断哪位是医生"
        "（询问病情、下诊断、开检查、开药、嘱咐复诊的一方），哪位是患者（描述症状的一方）。\n\n"
        + "\n\n".join(groups)
        + '\n\n只输出 JSON，例如 {"doctor": 1}；判断不了就输出 {"doctor": -1}。'
    )
    try:
        text = await _call(
            [{"role": "system", "content": "你只输出 JSON，不要解释。"},
             {"role": "user", "content": prompt}],
            settings.llm_model,
            temperature=0.0,
            max_tokens=30,
            timeout=25,
        )
    except HTTPException:
        return None
    m = re.search(r'"doctor"\s*:\s*(-?\d+)', text)
    if not m:
        return None
    value = int(m.group(1))
    return value if value in ids else None


def _role_map(sentences: list[dict], doctor: int | None, ids: list[int]) -> dict[int, str]:
    if len(ids) == 1:
        return {ids[0]: "患者"}
    if doctor is None:
        doctor = ids[1] if len(ids) > 1 else ids[0]
    return {i: ("医生" if i == doctor else "患者") for i in ids}


def _merge_sentences(sentences: list[dict], roles: dict[int, str]) -> list[dict]:
    """同一说话人的相邻短句合并，读起来像一段话，而不是碎句。"""
    out: list[dict] = []
    for s in sentences:
        sp = roles.get(s["speaker_id"], "患者")
        text = s["text"]
        if out and out[-1]["speaker"] == sp and s["begin"] - out[-1]["end"] <= 1200 \
                and len(out[-1]["text"]) < 160:
            out[-1]["text"] += text
            out[-1]["end"] = s["end"]
        else:
            out.append({"speaker": sp, "text": text, "begin": s["begin"], "end": s["end"]})
    return out


@router.post("/asr/speakers", dependencies=[Depends(rate_limit("ai:asr:speakers", 12))])
async def asr_speakers(body: dict, _user: User = Depends(current_user)) -> dict:
    """录音转写并按「医生 / 患者」分行。

    前端录音（webm/opus、mp4、wav）→ 临时存储 → 非实时转写（说话人分离）
    → 判断角色 → 合并成对话行。任一步失败，前端会退回普通转写。
    """
    audio = str(body.get("audio") or "").strip()
    raw, ext = _audio_bytes(audio)
    sentences = await _filetrans(raw, ext)
    if not sentences:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "没有识别到内容")

    ids: list[int] = []
    for s in sentences:
        try:
            sid = int(s["speaker_id"])
        except (KeyError, TypeError, ValueError):
            sid = 0
        s["speaker_id"] = sid
        if sid not in ids:
            ids.append(sid)
    if not ids:
        ids = [0]

    doctor = await _ask_doctor(sentences, ids)
    if doctor is None:
        doctor = _guess_doctor(sentences, ids)
    roles = _role_map(sentences, doctor, ids)
    segments = _merge_sentences(sentences, roles)

    text = "\n".join(f"{x['speaker']}：{x['text']}" for x in segments)
    return {
        "text": text[:4000],
        "segments": segments[:200],
        "speakers": len(ids),
        "engine": "diarize",
    }

