"""模型适配层。

业务代码只调 `complete()`，不关心是哪家供应商。这里统一处理：
  · 超时与总时限（每次调用必须有上限）
  · 指数退避 + 随机抖动的重试（避免重试风暴）
  · 连续失败后的熔断与半开探针
  · 主模型不可用时的备用模型
  · 并发上限
  · 每次调用（含失败）都写 token_usage

没有配置 LLM_API_KEY 时抛 LLMNotConfigured，调用方退回规则式回答——
服务照样可用，只是措辞朴素。**绝不静默假装调用成功**。
"""

import asyncio
import logging
import random
import time

import httpx

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TokenUsage

logger = logging.getLogger("app.llm")

# 粗略单价（美元 / 1K tokens），仅用于成本量级观察，不用于计费
PRICE_PER_1K = {
    "deepseek-chat": (0.00014, 0.00028),
    "gpt-4o-mini": (0.00015, 0.0006),
}


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class _Breaker:
    """简单的连续失败熔断：打开后冷却一段时间，再放一个探针。"""

    def __init__(self, threshold: int, cooldown: int) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True
        return (time.time() - self.opened_at) > self.cooldown

    def on_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.time()


_breakers: dict[str, _Breaker] = {}
_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
    return _semaphore


def _breaker(model: str) -> _Breaker:
    if model not in _breakers:
        _breakers[model] = _Breaker(settings.llm_breaker_threshold, settings.llm_breaker_cooldown)
    return _breakers[model]


def _record_usage(**kw) -> None:
    """用量落库不该拖慢请求，放到线程里做；失败只记日志。"""
    def _write():
        try:
            with SessionLocal() as db:
                db.add(TokenUsage(**kw))
                db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("token_usage 写入失败: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write)
    except RuntimeError:
        _write()


def is_configured() -> bool:
    return bool(settings.llm_api_key) and settings.llm_provider not in ("", "none")


async def _call_once(model: str, messages: list[dict], timeout: float,
                    temperature: float = 0.2, response_format: str = "",
                    max_tokens: int | None = None) -> tuple[str, dict]:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    if response_format:
        payload["response_format"] = {"type": response_format}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code == 429 or resp.status_code >= 500:
        raise LLMError(f"上游返回 {resp.status_code}", retryable=True, status=resp.status_code)
    if resp.status_code >= 400:
        # 4xx 重试没有意义（密钥错、参数错）
        raise LLMError(f"上游返回 {resp.status_code}", retryable=False, status=resp.status_code)

    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage") or {}
    return text, usage


async def complete(
    messages: list[dict],
    *,
    user_id: str,
    run_id: str | None = None,
    temperature: float = 0.2,
    response_format: str = "",
    max_tokens: int | None = None,
    timeout: float | None = None,
    model: str = "",
) -> str:
    if not is_configured():
        raise LLMNotConfigured("未配置模型")

    primary = (model or settings.llm_model).strip()
    models = [primary]
    for candidate in (settings.llm_fallback_model, settings.llm_model):
        if candidate and candidate not in models:
            models.append(candidate)

    last_error: Exception | None = None
    deadline = time.monotonic() + settings.llm_timeout_seconds * (settings.llm_max_retries + 1)

    async with _sem():
        for model in models:
            breaker = _breaker(model)
            if not breaker.allow():
                logger.warning("模型 %s 处于熔断状态，跳过", model)
                last_error = LLMError("circuit open", retryable=True)
                continue

            for attempt in range(settings.llm_max_retries + 1):
                if time.monotonic() > deadline:
                    break
                started = time.monotonic()
                try:
                    text, usage = await _call_once(
                        model, messages, timeout or settings.llm_timeout_seconds,
                        temperature=temperature, response_format=response_format,
                        max_tokens=max_tokens,
                    )
                    latency = int((time.monotonic() - started) * 1000)
                    breaker.on_success()

                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    price_in, price_out = PRICE_PER_1K.get(model, (0.0, 0.0))
                    cost = prompt_tokens / 1000 * price_in + completion_tokens / 1000 * price_out
                    _record_usage(
                        user_id=user_id, run_id=run_id, provider=settings.llm_provider,
                        model=model, prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens, cost_usd=round(cost, 6),
                        latency_ms=latency, ok=True,
                    )
                    logger.info("模型调用成功", extra={"model": model, "tokens": prompt_tokens + completion_tokens,
                                                      "cost_usd": round(cost, 6), "ms": latency})
                    return text.strip()

                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    breaker.on_failure()
                    logger.warning("模型调用超时/网络错误 model=%s attempt=%s: %s", model, attempt, exc)
                except LLMError as exc:
                    last_error = exc
                    breaker.on_failure()
                    logger.warning("模型调用失败 model=%s attempt=%s: %s", model, attempt, exc)
                    if not exc.retryable:
                        break
                except Exception as exc:  # pragma: no cover
                    last_error = exc
                    breaker.on_failure()
                    logger.exception("模型调用未知错误 model=%s", model)

                # 指数退避 + 抖动
                backoff = min(8.0, 0.4 * (2 ** attempt)) * (0.5 + random.random())
                await asyncio.sleep(backoff)

    _record_usage(
        user_id=user_id, run_id=run_id, provider=settings.llm_provider,
        model=settings.llm_model, ok=False, error=str(last_error)[:400],
    )
    raise LLMError(f"模型调用失败：{last_error}")
