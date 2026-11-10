"""米家极客版集成常量。"""
from __future__ import annotations

DOMAIN = "mijia_geek"
NAME = "米家极客版"

# 配置项
CONF_BRIDGE_URL = "bridge_url"
CONF_API_TOKEN = "api_token"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_BRIDGE_URL = "http://localhost:8099"

# 服务
SERVICE_REFRESH = "refresh"
SERVICE_TOGGLE_RULE = "toggle_rule"
SERVICE_SET_ENABLED = "set_rule_enabled"
SERVICE_GET_GRAPH = "get_rule_detail"
SERVICE_GET_LOG = "get_log"
SERVICE_GET_LOG_ALL = "get_log_all"
SERVICE_DELETE_RULE = "delete_rule"
SERVICE_SET_VAR = "set_variable"
SERVICE_GET_VAR = "get_variable"
SERVICE_GET_VAR_SCOPES = "get_variable_scopes"
SERVICE_CREATE_VAR = "create_variable"
SERVICE_DELETE_VAR = "delete_variable"
SERVICE_SAVE_RULE = "save_rule"
SERVICE_SET_PASSCODE = "set_passcode"

# 事件
EVENT_PUSH = "mijia_geek_push"

# 信号
SIGNAL_NEW_RULE = "mijia_geek_new_rule"
