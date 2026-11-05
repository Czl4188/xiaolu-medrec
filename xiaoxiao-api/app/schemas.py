from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.crypto import normalize_phone


# ————————————————— 鉴权 —————————————————
class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    phone: str = Field(min_length=11, max_length=20)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("用户名不能为空")
        if any(ch in v for ch in "/\\\n\r\t"):
            raise ValueError("用户名不能包含斜杠或控制字符")
        return v

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str) -> str:
        digits = normalize_phone(v)
        if len(digits) != 11 or not digits.startswith("1") or digits[1] not in "3456789":
            raise ValueError("手机号要填 11 位，1 开头")
        return digits


class LoginIn(BaseModel):
    """手机号是登录账号；username 只给还没绑手机号的老账号用。"""

    phone: str = ""
    username: str = ""
    password: str

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("username")
    @classmethod
    def _clean_login_username(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _need_one_identity(self):
        if not self.phone and not self.username:
            raise ValueError("请填手机号")
        return self


class BindPhoneIn(BaseModel):
    phone: str = Field(min_length=11, max_length=20)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("phone")
    @classmethod
    def _clean_bind_phone(cls, v: str) -> str:
        digits = normalize_phone(v)
        if len(digits) != 11 or not digits.startswith("1") or digits[1] not in "3456789":
            raise ValueError("手机号要填 11 位，1 开头")
        return digits


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str = ""
    # 只对本人返回明文；由路由层解密后填入
    phone: str = ""
    phone_bound: bool = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ————————————————— 资料 —————————————————
class ProfileIn(BaseModel):
    name: str = Field(default="", max_length=32)
    age: str = Field(default="", max_length=8)
    gender: str = Field(default="", max_length=8)
    hospital: str = Field(default="", max_length=64)
    phone: str = Field(default="", max_length=20)
    idcard: str = Field(default="", max_length=18)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        digits = v.replace(" ", "").replace("-", "")
        if not digits.isdigit() or len(digits) != 11 or not digits.startswith("1"):
            raise ValueError("手机号应为 11 位数字")
        return digits

    @field_validator("idcard")
    @classmethod
    def _check_idcard(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            return ""
        if len(v) != 18 or not v[:17].isdigit() or (not v[17].isdigit() and v[17] != "X"):
            raise ValueError("身份证号应为 18 位，末位可以是 X")
        return v


class ProfileOut(ProfileIn):
    model_config = ConfigDict(from_attributes=True)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountIn(BaseModel):
    """注销必须再验一次密码。

    令牌可能被窃取；注销会删除全部病历，代价不可逆，
    所以不能只凭一个长期有效的会话令牌就执行。
    """

    password: str = Field(min_length=1, max_length=128)


class DeleteAccountOut(BaseModel):
    ok: bool = True
    records: int = 0
    sessions: int = 0
    runs: int = 0


# ————————————————— 就诊记录 —————————————————
class MedIn(BaseModel):
    name: str = ""
    dose: str = ""
    freq: str = ""
    timing: str = ""
    days: str = ""
    # 这味药在原文里的依据，供医生/本人核对
    evidence: str = ""


class RecordIn(BaseModel):
    client_id: str = Field(default="", max_length=48)
    visit_at: datetime
    hospital: str = ""
    summary: str = ""
    transcript: str = ""
    meds: list[MedIn] = Field(default_factory=list)
    follow_up: str = ""
    allergy: str = ""
    notes: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    discontinued: list[str] = Field(default_factory=list)
    allergy_denied: bool = False
    allergens: list[dict] = Field(default_factory=list)
    reading: str = ""
    self_check: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    uncertain: list[str] = Field(default_factory=list)
    mode: str = "manual"


class RecordPatch(BaseModel):
    """归档后修正字段：只改传进来的键。"""
    visit_at: datetime | None = None
    hospital: str | None = None
    summary: str | None = None
    transcript: str | None = None
    meds: list["MedIn"] | None = None
    follow_up: str | None = None
    allergy: str | None = None
    notes: list[str] | None = None
    conditions: list[str] | None = None
    discontinued: list[str] | None = None
    allergy_denied: bool | None = None
    allergens: list[dict] | None = None


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    client_id: str
    visit_at: datetime
    hospital: str
    summary: str
    transcript: str
    meds: list[dict]
    follow_up: str
    allergy: str
    notes: list[str]
    conditions: list[str] = []
    discontinued: list[str] = []
    allergy_denied: bool = False
    allergens: list[dict] = []
    reading: str = ""
    self_check: list[str] = []
    confidence: float = 0.0
    uncertain: list[str] = []
    mode: str
    created_at: datetime


class ExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class ExtractOut(BaseModel):
    visit_at: datetime
    visit_hint: datetime | None = None
    hospital: str
    summary: str
    meds: list[MedIn]
    follow_up: str
    allergy: str
    allergy_denied: bool = False
    notes: list[str]
    conditions: list[str] = []
    discontinued: list[str] = []
    allergens: list[dict] = []
    reading: str = ""
    self_check: list[str] = []
    confidence: float = 0.0
    uncertain: list[str] = []
    source: str = "rule"
    holes: list[str]
    transcript: str


# ————————————————— 对话 —————————————————
class ChatTurn(BaseModel):
    """前端带上来的历史轮次：只用于消解指代，不作为事实来源。"""

    role: str = Field(default="user", max_length=16)
    content: str = Field(default="", max_length=200)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(default="", max_length=80)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)


class Citation(BaseModel):
    record_id: str
    client_id: str = ""
    visit_at: datetime
    hospital: str = ""


class AnswerOut(BaseModel):
    answer: str
    citations: list[Citation] = []
    refused: bool = False
    # smalltalk / crisis / emergency / out_of_scope / clarify；空串 = 档案作答
    kind: str = ""
    disclaimer: str = "本内容仅为病历档案整理，不能替代医生诊疗意见。"


# ————————————————— 任务 —————————————————
class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: str = ""


class JobAccepted(BaseModel):
    run_id: str
    status: str
    poll_url: str
