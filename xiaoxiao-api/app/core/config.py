"""集中配置：全部来自环境变量，代码里不出现密钥。

默认值只保证「本机可直接跑起来」（SQLite）——生产必须显式给出
DATABASE_URL / SECRET_KEY / LLM_API_KEY，缺失时启动日志会告警。

除了 `.env`，也会读同目录的 `config.env`：部分部署打包会忽略点文件，
用普通文件名兜底，避免线上模型配置凭空消失。
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_extra_env() -> None:
    for name in (".env", "config.env"):
        path = Path(name)
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            # 真实环境变量优先，文件只做兜底
            os.environ.setdefault(key.strip(), value.strip())


_load_extra_env()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # —— 基础 ——
    app_env: str = "dev"                 # dev | staging | prod
    app_name: str = "xiaolu-medrec-api"
    debug: bool = False

    # —— 安全 ——
    secret_key: str = "dev-only-insecure-change-me"
    # 一次性数据迁移用的管理令牌；留空则 /api/admin/* 直接 404
    admin_token: str = ""
    access_token_ttl_seconds: int = 60 * 60 * 24 * 7
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    max_request_mb: int = 2              # 普通 JSON 请求体上限
    max_upload_mb: int = 25              # 音频上传上限

    # —— 数据 ——
    # 生产：postgresql+psycopg://user:pass@host:5432/db
    database_url: str = "sqlite:///./data/app.db"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    redis_url: str = ""                  # 留空则限流退化为进程内

    # —— 限流 ——
    rate_limit_per_minute: int = 60
    auth_rate_limit_per_minute: int = 12

    # —— 模型 ——
    llm_provider: str = "none"           # none | openai | deepseek
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # 理解引擎（summarize / answer）默认用更快的小模型；留空则跟随 llm_model
    engine_model: str = "qwen-flash"
    # 提问归一（TASK=route）：更轻的分类任务，超时就降级
    normalize_model: str = ""            # 留空跟随 engine_model
    normalize_timeout: float = 2.5
    normalize_min_confidence: float = 0.6
    # 对话层（闲聊 / 常识 / 情绪）：0 = 关闭，行为回到固定话术（回滚开关）
    llm_smalltalk: int = 1
    smalltalk_timeout: float = 12.0
    # 档案作答的措辞温度：只影响说法，不影响事实；0 = 回到逐字复述式
    answer_temperature: float = 0.35
    # 自然语言唤起动作：0 = 关闭（不识别、不出确认气泡）
    nl_action: int = 0
    nl_action_timeout: float = 5.0
    # 日期/「距今几天」按这个时区算，避免服务器在 UTC 时把「今天」答错一天
    app_timezone: str = "Asia/Shanghai"
    llm_vision_model: str = "qwen3-vl-plus"   # 识图模型（兼容 OpenAI 多模态格式）
    llm_fallback_model: str = ""         # 主模型熔断后的备用模型
    llm_timeout_seconds: float = 25.0
    llm_max_retries: int = 2
    llm_max_concurrency: int = 8
    llm_breaker_threshold: int = 5       # 连续失败多少次后短路
    llm_breaker_cooldown: int = 30       # 短路多久后放一个探针

    # —— ASR ——
    asr_provider: str = "none"           # none | openai
    asr_model: str = "whisper-1"
    # 语音转写走「能听音频的对话模型」：和文本模型共用 base_url / key
    asr_chat_model: str = "qwen3.8-omni-flash"
    # 说话人分离：走 DashScope 非实时转写（异步任务），和聊天模型分开配置
    asr_diarize_base: str = "https://dashscope.aliyuncs.com"
    asr_diarize_model: str = "qwen-audio-3.0-asr-flash-filetrans"
    asr_diarize_timeout: float = 75.0
    # 语音朗读：qwen3-tts-flash 走 DashScope 多模态接口，返回音频 URL
    tts_model: str = "qwen3-tts-flash"
    tts_voice: str = "Cherry"

    # —— 任务 ——
    job_poll_interval: float = 1.0
    job_max_attempts: int = 5
    worker_id: str = ""

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in ("prod", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
