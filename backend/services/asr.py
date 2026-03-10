"""
AI 语音任务处理系统 - FunASR 语音转写服务

通过 WebSocket 连接阿里 FunASR 服务，将音频文件转写为文本。
"""
import asyncio
import json
import logging
from pathlib import Path

import websockets

from backend.config import FUNASR_HOST, FUNASR_PORT, FUNASR_MODE

logger = logging.getLogger(__name__)

# 音频分块大小（字节），每次发送 10KB
CHUNK_SIZE = 10240

# WebSocket 连接与接收超时（秒）
WS_CONNECT_TIMEOUT = 10
WS_RECV_TIMEOUT = 60


async def transcribe(audio_path: str) -> str:
    """将音频文件发送给 FunASR WebSocket 服务进行语音转写。

    Args:
        audio_path: 音频文件的绝对路径。

    Returns:
        转写后的完整文本字符串。

    Raises:
        FileNotFoundError: 音频文件不存在。
        ConnectionError: 无法连接到 FunASR 服务。
        RuntimeError: 转写过程中发生错误。
    """
    audio_file = Path(audio_path)
    if not audio_file.exists():
        logger.error("音频文件不存在: %s", audio_path)
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    file_size = audio_file.stat().st_size
    filename = audio_file.name
    suffix = audio_file.suffix.lstrip(".").lower()
    wav_format = suffix if suffix else "wav"

    logger.info(
        "开始转写音频文件: %s (大小: %d bytes, 格式: %s)",
        filename,
        file_size,
        wav_format,
    )

    uri = f"ws://{FUNASR_HOST}:{FUNASR_PORT}"
    transcript_parts: list[str] = []

    try:
        async with websockets.connect(  # type: ignore[attr-defined]
            uri, open_timeout=WS_CONNECT_TIMEOUT
        ) as ws:
            logger.debug("已连接到 FunASR 服务: %s", uri)

            # ---- 1. 发送初始配置消息 ----
            init_message = json.dumps(
                {
                    "mode": FUNASR_MODE,
                    "wav_name": filename,
                    "wav_format": wav_format,
                    "is_speaking": True,
                }
            )
            await ws.send(init_message)
            logger.debug("已发送初始化消息: %s", init_message)

            # ---- 2. 分块发送音频数据 ----
            audio_data = audio_file.read_bytes()
            total_chunks = (len(audio_data) + CHUNK_SIZE - 1) // CHUNK_SIZE

            for i in range(0, len(audio_data), CHUNK_SIZE):
                chunk = audio_data[i : i + CHUNK_SIZE]
                await ws.send(chunk)

            chunk_count = total_chunks
            logger.debug("已发送 %d 个音频数据块", chunk_count)

            # ---- 3. 发送结束信号 ----
            end_message = json.dumps({"is_speaking": False})
            await ws.send(end_message)
            logger.debug("已发送结束信号")

            # ---- 4. 接收转写结果 ----
            while True:
                try:
                    response = await asyncio.wait_for(
                        ws.recv(), timeout=WS_RECV_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("接收转写结果超时，停止等待")
                    break

                if isinstance(response, bytes):
                    response = response.decode("utf-8")

                try:
                    result = json.loads(response)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 响应: %s", response[:200])
                    continue

                logger.debug("收到 FunASR 响应: %s", json.dumps(result, ensure_ascii=False)[:300])

                # 提取文本内容
                text = result.get("text", "")
                if text:
                    transcript_parts.append(text)

                # 判断是否为最终结果（FunASR 离线模式通常只返回一条最终结果）
                is_final = result.get("is_final", False)
                mode_info = result.get("mode", "")

                # 离线模式收到结果即可结束；在线/2pass 模式等待 is_final
                if FUNASR_MODE == "offline" or is_final:
                    logger.debug("收到最终转写结果，结束接收")
                    break

    except websockets.exceptions.WebSocketException as e:
        logger.error("FunASR WebSocket 连接异常: %s", str(e))
        raise ConnectionError(f"无法连接到 FunASR 服务 ({uri}): {e}") from e
    except OSError as e:
        logger.error("FunASR 网络连接失败: %s", str(e))
        raise ConnectionError(f"无法连接到 FunASR 服务 ({uri}): {e}") from e

    transcript = "".join(transcript_parts)

    if not transcript:
        logger.warning("转写结果为空，音频文件可能无有效语音内容: %s", audio_path)
    else:
        logger.info(
            "转写完成: %s, 文本长度: %d 字符",
            filename,
            len(transcript),
        )

    return transcript
