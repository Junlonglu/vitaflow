from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class InfoFragmentIn(BaseModel):
    kind: str = Field(min_length=1, max_length=24)
    payload: dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="manual", max_length=20)


class InfoFragmentUpdateIn(BaseModel):
    kind: Optional[str] = Field(default=None, max_length=24)
    payload: Optional[dict[str, Any]] = None
    title: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[list[str]] = None


class InfoFragmentBulkIn(BaseModel):
    items: list[InfoFragmentIn] = Field(default_factory=list)


class InfoFragmentExtractIn(BaseModel):
    resume_id: int


class InfoFragmentApplyIn(BaseModel):
    resume_id: int
    fragment_ids: list[int] = Field(default_factory=list)


class InfoFragmentOut(BaseModel):
    id: int
    kind: str
    kind_label: str
    title: str
    summary: str
    payload: dict[str, Any]
    tags: list[str]
    source: str
    enabled: bool
    usage_count: int
    last_used_time: Optional[datetime] = None
    update_time: Optional[datetime] = None
    create_time: Optional[datetime] = None


# ---------------------------------------------------------------------------
# AI service tool payloads（内部调用，带 user_id，不做登录态校验）
# ---------------------------------------------------------------------------


class InfoFragmentProposalIn(BaseModel):
    id: Optional[int] = None
    kind: str = Field(min_length=1, max_length=24)
    payload: dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)


class ServiceProposeFragmentsRequest(BaseModel):
    user_id: int
    resume_id: Optional[int] = None
    fragments: list[InfoFragmentProposalIn] = Field(default_factory=list)


class ServiceImportFragmentsRequest(BaseModel):
    user_id: int
    resume_id: int
    fragment_ids: list[int] = Field(default_factory=list)
