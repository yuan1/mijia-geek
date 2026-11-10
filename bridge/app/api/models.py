"""REST 请求模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConnectReq(BaseModel):
    passcode: str
    gateway_url: str | None = None


class VarReq(BaseModel):
    """兼容旧接口：POST /api/variable。"""

    scope: str = "global"
    var_id: str
    value: Any


class SetVarReq(BaseModel):
    scope: str = "global"
    value: Any


class CreateVarReq(BaseModel):
    scope: str = "global"
    var_id: str
    var_type: str = "string"
    value: Any = None
    name: str = ""


class ToggleReq(BaseModel):
    enable: bool


class SaveRuleReq(BaseModel):
    nodes: list
    cfg: dict = Field(default_factory=dict)
    name: str | None = None
