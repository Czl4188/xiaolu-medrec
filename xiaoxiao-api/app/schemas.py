from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ————————————————— 鉴权 —————————————————
class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
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


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str = ""


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
    holes: list[str]
    transcript: str


# ————————————————— 对话 —————————————————
class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(default="", max_length=80)


class Citation(BaseModel):
    record_id: str
    client_id: str = ""
    visit_at: datetime
    hospital: str = ""


class AnswerOut(BaseModel):
    answer: str
    citations: list[Citation] = []
    refused: bool = False
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
