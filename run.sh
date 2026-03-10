#!/usr/bin/env bash
# AI 语音任务处理系统 - 启动脚本

# 加载 .env 文件（如果存在）
if [ -f .env ]; then
    echo "Loading environment variables from .env ..."
    set -a
    source .env
    set +a
fi

# 构建 SSL 参数
SSL_ARGS=""
if [ -n "$SSL_CERTFILE" ] && [ -n "$SSL_KEYFILE" ]; then
    SSL_ARGS="--ssl-certfile $SSL_CERTFILE --ssl-keyfile $SSL_KEYFILE"
    echo "SSL enabled: cert=$SSL_CERTFILE key=$SSL_KEYFILE"
    echo "Access via: https://<your-ip>:${SERVER_PORT:-8090}"
else
    echo "SSL disabled (set SSL_CERTFILE and SSL_KEYFILE in .env to enable)"
    echo "WARNING: Microphone will only work on localhost without HTTPS!"
    echo "Access via: http://localhost:${SERVER_PORT:-8090}"
fi

# 启动 uvicorn 服务
echo "Starting AI Voice Task Server ..."
uvicorn backend.main:app \
    --host "${SERVER_HOST:-0.0.0.0}" \
    --port "${SERVER_PORT:-8090}" \
    --reload \
    $SSL_ARGS
