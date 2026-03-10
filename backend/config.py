"""
AI 语音任务处理系统 - 配置模块
"""
import os
from pathlib import Path

# ============ 基础路径 ============
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TASKS_DIR = DATA_DIR / "tasks"
DATABASE_PATH = DATA_DIR / "app.db"

# 确保目录存在
TASKS_DIR.mkdir(parents=True, exist_ok=True)

# ============ 服务器配置 ============
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8090"))

# ============ FunASR 配置 ============
FUNASR_HOST = os.getenv("FUNASR_HOST", "localhost")
FUNASR_PORT = int(os.getenv("FUNASR_PORT", "10095"))
FUNASR_MODE = os.getenv("FUNASR_MODE", "offline")  # offline / online / 2pass

# ============ DashScope (阿里百炼) ASR 配置 ============
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_ASR_MODEL_REALTIME = os.getenv("DASHSCOPE_ASR_MODEL_REALTIME", "paraformer-realtime-v2")
DASHSCOPE_ASR_MODEL_FILE = os.getenv("DASHSCOPE_ASR_MODEL_FILE", "paraformer-v2")
DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DASHSCOPE_FILE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
DASHSCOPE_TASK_API_URL = "https://dashscope.aliyuncs.com/api/v1/tasks"

# 服务器公网地址（文件转写模式需要 DashScope 回调拉取音频）
SERVER_PUBLIC_URL = os.getenv("SERVER_PUBLIC_URL", "")

# ============ 总结模型配置 ============
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ============ 默认总结提示词 ============
DEFAULT_SUMMARY_PROMPT = """你是一个专业的文字内容整理助手。请对以下语音转写文本进行梳理和总结，输出需要包含：

## 主题概述
简要说明这段语音的主要内容和背景。

## 要点提炼
- 列出关键信息点（使用 bullet points）

## 行动项
- 如果有需要跟进的事项，请列出（如没有可省略此部分）

## 详细整理
对原文内容进行结构化整理，使其更易阅读。

请确保总结准确、完整，不遗漏重要信息。
"""

# ============ 文件保留策略 ============
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))

# ============ 文件大小限制 ============
MAX_AUDIO_SIZE_MB = int(os.getenv("MAX_AUDIO_SIZE_MB", "100"))

# ============ 音频格式 ============
ALLOWED_AUDIO_TYPES = {"audio/wav", "audio/webm", "audio/mp3", "audio/mpeg", "audio/ogg", "audio/mp4"}
