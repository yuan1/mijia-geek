"""Bridge API 辅助逻辑测试。"""
from __future__ import annotations


import pytest

from fastapi import HTTPException  # noqa: E402

from api.models import SaveRuleReq  # noqa: E402
from api.utils import (  # noqa: E402
    build_save_cfg,
    coerce_create_value,
    rule_user_data,
    validate_set_value,
    validate_var_type,
)


def test_build_save_cfg_forces_rule_id_and_defaults():
    cfg = build_save_cfg("rule_1", SaveRuleReq(nodes=[]))
    assert cfg["id"] == "rule_1"
    assert cfg["uiType"] == "graph"
    assert cfg["enable"] is False
    assert cfg["userData"]["name"] == "rule_1"
    assert cfg["userData"]["transform"] == {
        "x": 0, "y": 0, "scale": 1, "rotate": 0,
    }


def test_build_save_cfg_keeps_existing_transform():
    cfg = build_save_cfg(
        "rule_2",
        SaveRuleReq(nodes=[], cfg={
            "userData": {"name": "规则", "transform": {"x": 10, "y": 20,
                                                       "scale": 2, "rotate": 0}}
        }),
    )
    assert cfg["userData"]["name"] == "规则"
    assert cfg["userData"]["transform"]["x"] == 10


def test_create_var_value_defaults_and_validation():
    assert coerce_create_value("number", None) == 0
    assert coerce_create_value("string", None) == ""
    assert coerce_create_value("number", "26") == 26
    assert coerce_create_value("number", "26.5") == 26.5
    with pytest.raises(HTTPException):
        coerce_create_value("number", True)
    with pytest.raises(HTTPException):
        coerce_create_value("number", "abc")
    with pytest.raises(HTTPException):
        validate_var_type("bool")


def test_validate_set_value_rejects_nan():
    assert validate_set_value(0) == 0
    assert validate_set_value("x") == "x"
    with pytest.raises(HTTPException):
        validate_set_value(float("nan"))


def test_rule_user_data_defaults_and_preserves_layout():
    assert rule_user_data({}) == {
        "name": "",
        "lastUpdateTime": 0,
        "transform": {"x": 0, "y": 0, "scale": 1, "rotate": 0},
    }
    existing = {
        "name": "规则", "updateTime": 9,
        "userData": {"transform": {"x": 3, "y": 4, "scale": 1,
                                   "rotate": 0}},
    }
    data = rule_user_data(existing)
    assert data["name"] == "规则"
    assert data["lastUpdateTime"] == 9
    assert data["transform"] == {"x": 3, "y": 4, "scale": 1, "rotate": 0}
