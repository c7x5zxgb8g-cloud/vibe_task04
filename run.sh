#!/usr/bin/env bash
# AI 语音任务处理系统 - 启动脚本

# 加载 .env 文件（如果存在）
if [ -f .env ]; then
    echo "Loading environment variables from .env ..."
    set -a
    source .env
    set +a
fi

# 启动 uvicorn 服务
echo "Starting AI Voice Task Server ..."
uvicorn backend.main:app \
    --host "${SERVER_HOST:-0.0.0.0}" \
    --port "${SERVER_PORT:-8090}" \
    --reload
