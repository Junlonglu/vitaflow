from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.url_guard import literal_public_url

MAX_CONTEXT_WINDOW = 2_000_000
MAX_OUTPUT_TOKENS = 65_536
MAX_SKILL_INSTRUCTIONS = 20_000
MAX_MODELS_PER_USER = 10

# auto = 不下发任何推理参数（跟随模型默认，兼容所有接入点）；
# off  = 明确关闭；其余映射为 pi-agent thinkingLevel / reasoning_effort。
ReasoningEffort = Literal["auto", "off", "minimal", "low", "medium", "high", "xhigh"]
REASONING_EFFORTS: tuple[str, ...] = ("auto", "off", "minimal", "low", "medium", "high", "xhigh")
# 需要真正给模型下发 reasoning_effort 的档位。
REASONING_LEVELS: frozenset[str] = frozenset({"minimal", "low", "medium", "high", "xhigh"})


def _normalize_base_url(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("接入地址不能为空")
    if not raw.lower().startswith(("http://", "https://")):
        raise ValueError("接入地址必须以 http:// 或 https:// 开头")
    allowed, reason = literal_public_url(raw)
    if not allowed:
        raise ValueError(reason)
    return raw


class UserAiCredentialIn(BaseModel):
    provider_label: str = Field(default="自定义", max_length=60)
    base_url: str = Field(max_length=255)
    model: str = Field(max_length=120)
    # 留空表示沿用已存的密钥，避免「改模型名也要重填一把 key」
    api_key: Optional[str] = Field(default=None, max_length=400)
    context_window: int = Field(default=32_000, ge=1_024, le=MAX_CONTEXT_WINDOW)
    max_tokens: int = Field(default=4_096, ge=256, le=MAX_OUTPUT_TOKENS)
    supports_image: bool = False
    reasoning_effort: ReasoningEffort = "auto"
    is_default: bool = False
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _normalize_base_url(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = (value or "").strip()
        if not model:
            raise ValueError("模型名称不能为空")
        return model

    @field_validator("provider_label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return (value or "").strip() or "自定义"


class UserAiCredentialOut(BaseModel):
    id: int
    provider_label: str
    base_url: str
    model: str
    api_key_masked: str
    has_api_key: bool
    context_window: int
    max_tokens: int
    supports_image: bool
    reasoning_effort: ReasoningEffort
    is_default: bool
    enabled: bool
    last_verified_time: Optional[datetime] = None
    update_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserAiModelListOut(BaseModel):
    items: list[UserAiCredentialOut]
    total: int
    active: int
    default_id: Optional[int] = None


class UserAiCredentialTestResult(BaseModel):
    ok: bool
    detail: str
    models: list[str] = Field(default_factory=list)


class UserAiCredentialTestIn(BaseModel):
    """测试连接：允许先测未保存的配置；api_key 留空则用数据库里已存的密钥。"""

    base_url: str = Field(max_length=255)
    model: str = Field(max_length=120)
    api_key: Optional[str] = Field(default=None, max_length=400)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _normalize_base_url(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = (value or "").strip()
        if not model:
            raise ValueError("模型名称不能为空")
        return model


class UserSkillIn(BaseModel):
    name: str = Field(max_length=60)
    description: str = Field(default="", max_length=255)
    instructions: str = Field(min_length=1, max_length=MAX_SKILL_INSTRUCTIONS)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = (value or "").strip()
        if not name:
            raise ValueError("技能名称不能为空")
        return name

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("技能内容不能为空")
        return text


class UserSkillUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)
    description: Optional[str] = Field(default=None, max_length=255)
    instructions: Optional[str] = Field(default=None, max_length=MAX_SKILL_INSTRUCTIONS)
    enabled: Optional[bool] = None


class UserSkillOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    instructions: str
    enabled: bool
    source: str
    update_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserSkillPackageResult(BaseModel):
    """技能包导入结果：一次可能生成多个技能，被跳过的文件单独说明原因。"""

    items: list[UserSkillOut]
    total: int
    skipped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
