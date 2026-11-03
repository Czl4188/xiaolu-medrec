"""结构化 JSON 日志。

每条日志都带 request_id / user_id / run_id，便于按一次请求或一次 Agent
运行把 API、数据库、队列、Worker 串起来（对应架构文档 6.6）。
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
user_id_ctx: ContextVar[str] = ContextVar("user_id", default="-")
run_id_ctx: ContextVar[str] = ContextVar("run_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
            "user_id": user_id_ctx.get(),
            "run_id": run_id_ctx.get(),
        }
        for key in ("path", "method", "status", "ms", "event", "model", "tokens", "cost_usd"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn 自带的 access 日志会重复，交给中间件统一输出
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    logger.info(event, extra={"event": event, **fields})
