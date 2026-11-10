#!/usr/bin/env bash
set -e

echo "[INFO] 启动 Mijia Geek Bridge"

# 读取 addon 配置（登录码一次性，不再从配置读取/保存）
if [ -f /data/options.json ]; then
    export GATEWAY_URL=$(jq -r '.gateway_url // "http://192.168.1.100"' /data/options.json)
    export GATEWAY_WEB_URL=$(jq -r '.gateway_web_url // ""' /data/options.json)
    export SCAN_INTERVAL=$(jq -r '.scan_interval // 60' /data/options.json)
    export API_TOKEN=$(jq -r '.api_token // ""' /data/options.json)
    export MCP_HTTP_PORT=$(jq -r '.mcp_http_port // 0' /data/options.json)
    export MCP_ALLOW_WRITE=$(jq -r '.mcp_allow_write // false' /data/options.json)
    export MCP_ALLOW_PASSCODE=$(jq -r '.mcp_allow_passcode // false' /data/options.json)
else
    export GATEWAY_URL="${GATEWAY_URL:-http://192.168.1.100}"
    export GATEWAY_WEB_URL="${GATEWAY_WEB_URL:-}"
    export SCAN_INTERVAL="${SCAN_INTERVAL:-60}"
    export API_TOKEN="${API_TOKEN:-}"
    export MCP_HTTP_PORT="${MCP_HTTP_PORT:-0}"
    export MCP_ALLOW_WRITE="${MCP_ALLOW_WRITE:-0}"
    export MCP_ALLOW_PASSCODE="${MCP_ALLOW_PASSCODE:-0}"
fi

export DATA_DIR=/data

echo "[INFO] 网关地址: ${GATEWAY_URL}"
echo "[INFO] 扫描间隔: ${SCAN_INTERVAL}s"
echo "[INFO] 监听端口: 8099"
if [ -n "${API_TOKEN:-}" ]; then
    echo "[INFO] API Token: 已启用"
else
    echo "[INFO] API Token: 未设置（仅建议通过 ingress 访问）"
fi

MCP_PID=""
UVICORN_PID=""

cleanup() {
    trap - TERM INT
    for pid in "$MCP_PID" "$UVICORN_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    [ -n "$MCP_PID" ] && wait "$MCP_PID" 2>/dev/null || true
    [ -n "$UVICORN_PID" ] && wait "$UVICORN_PID" 2>/dev/null || true
}
trap 'cleanup; exit 143' TERM INT

if [ "${MCP_HTTP_PORT:-0}" != "0" ]; then
    if [ -z "${API_TOKEN:-}" ]; then
        echo "[ERROR] MCP_HTTP_PORT 已启用，但 API_TOKEN 为空；拒绝启动无鉴权 MCP HTTP" >&2
        exit 1
    fi
    echo "[INFO] MCP HTTP: 0.0.0.0:${MCP_HTTP_PORT}/mcp (Bearer auth)"
    MCP_ARGS=(--transport streamable-http --host 0.0.0.0
              --port "${MCP_HTTP_PORT}" --url http://127.0.0.1:8099
              --token "${API_TOKEN}" --http-token "${API_TOKEN}")
    if [ "${MCP_ALLOW_WRITE:-0}" = "1" ] || [ "${MCP_ALLOW_WRITE:-0}" = "true" ]; then
        MCP_ARGS+=(--allow-write)
    fi
    if [ "${MCP_ALLOW_PASSCODE:-0}" = "1" ] || [ "${MCP_ALLOW_PASSCODE:-0}" = "true" ]; then
        MCP_ARGS+=(--allow-passcode)
    fi
    python3 /app/mcp_server.py "${MCP_ARGS[@]}" &
    MCP_PID=$!
    sleep 1
    if ! kill -0 "$MCP_PID" 2>/dev/null; then
        echo "[ERROR] MCP HTTP 启动失败" >&2
        wait "$MCP_PID" || true
        exit 1
    fi
fi

cd /app
python3 -m uvicorn main:app --host 0.0.0.0 --port 8099 &
UVICORN_PID=$!

set +e
if [ -n "$MCP_PID" ]; then
    wait -n "$UVICORN_PID" "$MCP_PID"
    STATUS=$?
else
    wait "$UVICORN_PID"
    STATUS=$?
fi
set -e
cleanup
exit "$STATUS"
