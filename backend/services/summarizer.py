"""
AI 语音任务处理系统 - LLM 总结服务

通过 OpenAI 兼容 API 对转写文本进行智能总结。
"""
import logging
from typing import Optional

import httpx

from backend.config import LLM_API_BASE, LLM_API_KEY, LLM_MODEL, DEFAULT_SUMMARY_PROMPT

logger = logging.getLogger(__name__)

# HTTP 请求超时配置（秒）
REQUEST_TIMEOUT = 120


async def summarize(
    transcript: str,
    prompt_template: Optional[str] = None,
) -> str:
    """使用 LLM 对转写文本进行总结。

    Args:
        transcript: 语音转写后的文本内容。
        prompt_template: 自定义总结提示词模板。如果为 None，则使用默认提示词。

    Returns:
        LLM 生成的总结文本。

    Raises:
        ValueError: 转写文本为空。
        RuntimeError: LLM API 调用失败。
    """
    if not transcript or not transcript.strip():
        logger.error("转写文本为空，无法进行总结")
        raise ValueError("转写文本为空，无法进行总结")

    # 使用默认提示词或自定义提示词
    prompt = prompt_template if prompt_template else DEFAULT_SUMMARY_PROMPT

    # 拼接提示词和转写文本
    user_content = f"{prompt}\n\n以下是需要整理的语音转写文本：\n\n{transcript}"

    logger.info(
        "开始调用 LLM 总结, 模型: %s, 文本长度: %d 字符",
        LLM_MODEL,
        len(transcript),
    )

    # 构造 OpenAI 兼容的请求体
    request_body = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是一个专业的文字内容整理助手。",
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "temperature": 0.3,
    }

    headers = {
        "Content-Type": "application/json",
    }
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    url = f"{LLM_API_BASE}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            logger.debug("发送请求到 LLM API: %s", url)
            response = await client.post(url, json=request_body, headers=headers)
            response.raise_for_status()

            result = response.json()
            logger.debug(
                "收到 LLM API 响应, 状态码: %d, usage: %s",
                response.status_code,
                result.get("usage", {}),
            )

    except httpx.TimeoutException as e:
        logger.error("LLM API 请求超时: %s", str(e))
        raise RuntimeError(f"LLM API 请求超时 ({REQUEST_TIMEOUT}s): {e}") from e
    except httpx.HTTPStatusError as e:
        logger.error(
            "LLM API 返回错误, 状态码: %d, 响应: %s",
            e.response.status_code,
            e.response.text[:500],
        )
        raise RuntimeError(
            f"LLM API 返回错误 (HTTP {e.response.status_code}): {e.response.text[:200]}"
        ) from e
    except httpx.RequestError as e:
        logger.error("LLM API 请求失败: %s", str(e))
        raise RuntimeError(f"LLM API 请求失败: {e}") from e

    # 解析响应内容
    try:
        choices = result.get("choices", [])
        if not choices:
            logger.error("LLM API 响应中没有 choices: %s", result)
            raise RuntimeError("LLM API 响应中没有有效的 choices")

        summary = choices[0].get("message", {}).get("content", "")
        if not summary:
            logger.error("LLM API 响应中没有内容: %s", result)
            raise RuntimeError("LLM API 响应中没有有效的内容")

    except (KeyError, IndexError) as e:
        logger.error("解析 LLM API 响应失败: %s, 原始响应: %s", str(e), result)
        raise RuntimeError(f"解析 LLM API 响应失败: {e}") from e

    logger.info("LLM 总结完成, 总结文本长度: %d 字符", len(summary))
    return summary
