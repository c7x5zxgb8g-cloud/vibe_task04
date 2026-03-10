"""
AI 语音任务处理系统 - DashScope (阿里百炼) 语音转写服务

支持两种模式:
1. 文件转写 (REST API): 上传完成后异步转写
2. 实时转写 (WebSocket): 流式音频实时转写
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path

import httpx
import websockets

from backend.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_ASR_MODEL_FILE,
    DASHSCOPE_ASR_MODEL_REALTIME,
    DASHSCOPE_FILE_API_URL,
    DASHSCOPE_TASK_API_URL,
    DASHSCOPE_WS_URL,
)

logger = logging.getLogger(__name__)

# ============ 文件转写常量 ============
POLL_INTERVAL = 3       # 轮询间隔（秒）
POLL_TIMEOUT = 600      # 最大等待时间（秒）
HTTP_TIMEOUT = 30       # HTTP 请求超时（秒）


# ============================================================
#  文件转写 (REST API)
# ============================================================

async def transcribe_file(audio_path: str, audio_serve_url: str) -> str:
    """通过 DashScope REST API 进行文件转写。

    Args:
        audio_path: 音频文件的本地绝对路径（用于验证存在性）。
        audio_serve_url: 音频文件的公网可访问 URL。

    Returns:
        转写后的完整文本。

    Raises:
        FileNotFoundError: 音频文件不存在。
        ValueError: 配置缺失。
        RuntimeError: API 调用或转写失败。
    """
    audio_file = Path(audio_path)
    if not audio_file.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未配置，请在 .env 中设置")

    if not audio_serve_url:
        raise ValueError("音频文件公网 URL 不可用，请配置 SERVER_PUBLIC_URL")

    logger.info("DashScope 文件转写: 提交任务, URL=%s", audio_serve_url)

    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }

    body = {
        "model": DASHSCOPE_ASR_MODEL_FILE,
        "input": {
            "file_urls": [audio_serve_url]
        },
        "parameters": {
            "language_hints": ["zh", "en"]
        },
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # 步骤 1: 提交转写任务
        resp = await client.post(DASHSCOPE_FILE_API_URL, json=body, headers=headers)
        resp.raise_for_status()
        result = resp.json()

        task_id = result.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope 未返回 task_id: {result}")

        logger.info("DashScope 文件转写: 任务已提交, task_id=%s", task_id)

        # 步骤 2: 轮询等待完成
        poll_headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
        poll_url = f"{DASHSCOPE_TASK_API_URL}/{task_id}"
        elapsed = 0

        while elapsed < POLL_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            poll_resp = await client.get(poll_url, headers=poll_headers)
            poll_resp.raise_for_status()
            poll_result = poll_resp.json()

            task_status = poll_result.get("output", {}).get("task_status", "")
            logger.debug("DashScope 任务状态: %s (已等待 %ds)", task_status, elapsed)

            if task_status == "SUCCEEDED":
                # 步骤 3: 提取转写结果 URL 并获取文本
                results = poll_result.get("output", {}).get("results", [])
                if not results:
                    raise RuntimeError("DashScope 转写结果为空")

                transcription_url = results[0].get("transcription_url", "")
                if not transcription_url:
                    raise RuntimeError("DashScope 未返回 transcription_url")

                trans_resp = await client.get(transcription_url)
                trans_resp.raise_for_status()
                trans_data = trans_resp.json()

                transcript = _extract_text_from_file_result(trans_data)
                logger.info("DashScope 文件转写完成, 文本长度: %d", len(transcript))
                return transcript

            elif task_status == "FAILED":
                error_msg = poll_result.get("output", {}).get("message", "未知错误")
                raise RuntimeError(f"DashScope 转写失败: {error_msg}")

            # PENDING / RUNNING -> 继续轮询

        raise RuntimeError(f"DashScope 转写超时 ({POLL_TIMEOUT}s)")


def _extract_text_from_file_result(trans_data: dict) -> str:
    """从 DashScope 文件转写结果 JSON 中提取纯文本。"""
    texts = []

    transcripts = trans_data.get("transcripts", [])
    for t in transcripts:
        # 优先从 sentences 提取
        sentences = t.get("sentences", [])
        for sentence in sentences:
            text = sentence.get("text", "")
            if text:
                texts.append(text)

    # 兜底：直接取 text 字段
    if not texts:
        for t in transcripts:
            text = t.get("text", "")
            if text:
                texts.append(text)

    return "".join(texts)


# ============================================================
#  实时转写 (WebSocket)
# ============================================================

class RealtimeASRSession:
    """管理一个实时 ASR WebSocket 会话，作为前端与 DashScope 之间的代理。

    Usage:
        session = RealtimeASRSession()
        await session.connect(on_partial=callback)
        await session.send_audio_chunk(chunk_bytes)
        ...
        transcript = await session.finish()
        await session.close()
    """

    def __init__(self, sample_rate: int = 16000, audio_format: str = "pcm"):
        self.sample_rate = sample_rate
        self.audio_format = audio_format
        self._ws = None
        self._task_id = str(uuid.uuid4())
        self._transcript_parts: list[str] = []
        self._receive_task: asyncio.Task | None = None
        self._on_partial_callback = None
        self._finished = False
        self._connected = False

    async def connect(self, on_partial=None):
        """连接到 DashScope WebSocket 并发送 run-task 消息。

        Args:
            on_partial: 可选异步回调函数 async def callback(text: str)，
                        每次收到部分结果时调用。
        """
        if not DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY 未配置")

        self._on_partial_callback = on_partial

        headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}

        self._ws = await websockets.connect(
            DASHSCOPE_WS_URL,
            additional_headers=headers,
            open_timeout=10,
        )
        logger.info("DashScope 实时 ASR: WebSocket 已连接")

        # 发送 run-task 消息
        run_task_msg = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": DASHSCOPE_ASR_MODEL_REALTIME,
                "parameters": {
                    "format": self.audio_format,
                    "sample_rate": self.sample_rate,
                    "language_hints": ["zh", "en"],
                },
                "input": {},
            },
        }
        await self._ws.send(json.dumps(run_task_msg))
        logger.debug("DashScope 实时 ASR: run-task 消息已发送")

        # 启动后台接收循环
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._connected = True

    async def send_audio_chunk(self, chunk: bytes):
        """发送一个音频数据块。"""
        if self._ws and self._connected and not self._finished:
            try:
                await self._ws.send(chunk)
            except Exception as e:
                logger.warning("发送音频数据块失败: %s", str(e))

    async def finish(self) -> str:
        """发送 finish-task 信号并等待最终结果。"""
        if self._ws and self._connected and not self._finished:
            self._finished = True
            try:
                finish_msg = {
                    "header": {
                        "action": "finish-task",
                        "task_id": self._task_id,
                        "streaming": "duplex",
                    },
                    "payload": {
                        "input": {},
                    },
                }
                await self._ws.send(json.dumps(finish_msg))
                logger.debug("DashScope 实时 ASR: finish-task 消息已发送")
            except Exception as e:
                logger.warning("发送 finish-task 消息失败: %s", str(e))

        # 等待接收循环完成
        if self._receive_task:
            try:
                await asyncio.wait_for(self._receive_task, timeout=30)
            except asyncio.TimeoutError:
                logger.warning("DashScope 实时 ASR: 等待最终结果超时")
            except asyncio.CancelledError:
                pass

        return "".join(self._transcript_parts)

    async def close(self):
        """关闭 WebSocket 连接。"""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("DashScope 实时 ASR: 会话已关闭")

    async def _receive_loop(self):
        """后台循环接收 DashScope 返回的转写结果。"""
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 响应: %s", message[:200])
                    continue

                header = data.get("header", {})
                payload = data.get("payload", {})
                event = header.get("event", "")

                if event == "result-generated":
                    output = payload.get("output", {})
                    sentence = output.get("sentence", {})
                    text = sentence.get("text", "")

                    if text:
                        # 通知部分结果
                        if self._on_partial_callback:
                            try:
                                await self._on_partial_callback(text)
                            except Exception:
                                pass

                    # 检查句子是否为 final（有明确的 end_time）
                    is_sentence_end = sentence.get("end_time") is not None
                    if is_sentence_end and text:
                        self._transcript_parts.append(text)

                elif event == "task-started":
                    logger.info("DashScope 实时 ASR: 任务已启动, task_id=%s", self._task_id)

                elif event == "task-finished":
                    logger.info("DashScope 实时 ASR: 任务已完成")
                    break

                elif event == "task-failed":
                    error_code = header.get("error_code", "")
                    error_msg = header.get("error_message", "未知错误")
                    logger.error(
                        "DashScope 实时 ASR 任务失败: code=%s, msg=%s",
                        error_code,
                        error_msg,
                    )
                    break

        except websockets.exceptions.ConnectionClosed:
            logger.info("DashScope 实时 ASR: WebSocket 连接已关闭")
        except asyncio.CancelledError:
            logger.debug("DashScope 实时 ASR: 接收循环被取消")
            raise
        except Exception as e:
            logger.error("DashScope 实时 ASR 接收循环异常: %s", str(e))
