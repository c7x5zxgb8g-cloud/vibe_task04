"""
AI 语音任务处理系统 - 任务路由

包含：
- 文件上传与任务创建
- 任务状态查询
- ZIP 下载
- 音频文件服务（供 DashScope 文件转写回调）
- 实时 ASR WebSocket 代理
- 历史任务列表
"""
import json
import logging
import time
import uuid
from datetime import datetime, timedelta

import aiofiles
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import File as FileParam
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import (
    ALLOWED_AUDIO_TYPES,
    DEFAULT_SUMMARY_PROMPT,
    MAX_AUDIO_SIZE_MB,
    SERVER_PUBLIC_URL,
    TASKS_DIR,
)
from backend.database import SessionLocal, get_db
from backend.models import SystemConfig, Task, TaskStatus
from backend.services.packager import package_task
from backend.services.summarizer import summarize

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/task", tags=["task"])

# ============ Pydantic 模型 ============


class UploadResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    created_at: datetime
    expires_at: datetime
    has_zip: bool


class TaskListItem(BaseModel):
    id: str
    status: str
    created_at: datetime
    expires_at: datetime
    zip_filename: str | None


class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]
    total: int


# ============ 异步处理函数 ============


async def process_task(task_id: str, skip_transcribe: bool = False):
    """
    异步处理任务流程: transcribe -> summarize -> package
    使用独立的数据库会话，因为这是在后台任务中运行。

    Args:
        task_id: 任务 ID。
        skip_transcribe: 是否跳过转写步骤（实时模式下转写结果已在上传时保存）。
    """
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            logger.error("任务 %s 不存在，无法处理", task_id)
            return

        audio_path = str(TASKS_DIR / task_id / task.audio_filename)

        # ---- 步骤 1: 语音转写 ----
        if not skip_transcribe:
            try:
                logger.info("任务 %s: 开始语音转写 (模式: %s)", task_id, task.asr_mode)
                task.status = TaskStatus.TRANSCRIBING
                task.updated_at = datetime.utcnow()
                db.commit()

                if task.asr_mode == "file":
                    # DashScope 文件转写
                    from backend.services.asr_dashscope import transcribe_file

                    audio_serve_url = f"{SERVER_PUBLIC_URL}/api/task/{task_id}/audio"
                    transcript_text = await transcribe_file(audio_path, audio_serve_url)
                else:
                    # 默认使用 FunASR
                    from backend.services.asr import transcribe

                    transcript_text = await transcribe(audio_path)

                transcript_filename = f"{task_id}_transcript.txt"
                transcript_path = str(TASKS_DIR / task_id / transcript_filename)
                async with aiofiles.open(transcript_path, "w", encoding="utf-8") as f:
                    await f.write(transcript_text)

                task.transcript_filename = transcript_filename
                task.updated_at = datetime.utcnow()
                db.commit()
                logger.info("任务 %s: 语音转写完成", task_id)
            except Exception as e:
                logger.error("任务 %s: 语音转写失败 - %s", task_id, str(e))
                task.status = TaskStatus.FAILED
                task.error_message = f"语音转写失败: {str(e)}"
                task.updated_at = datetime.utcnow()
                db.commit()
                return
        else:
            # 实时模式：转写结果已保存，直接读取
            transcript_path = str(TASKS_DIR / task_id / task.transcript_filename)
            async with aiofiles.open(transcript_path, "r", encoding="utf-8") as f:
                transcript_text = await f.read()
            logger.info("任务 %s: 跳过转写（实时模式已提供），读取已有文本", task_id)

        # ---- 步骤 2: 文本总结 ----
        try:
            logger.info("任务 %s: 开始文本总结", task_id)
            task.status = TaskStatus.SUMMARIZING
            task.updated_at = datetime.utcnow()
            db.commit()

            # 从数据库获取自定义 prompt，没有则用默认
            config_row = (
                db.query(SystemConfig)
                .filter(SystemConfig.key == "summary_prompt")
                .first()
            )
            prompt_template = config_row.value if config_row else DEFAULT_SUMMARY_PROMPT

            summary_text = await summarize(transcript_text, prompt_template)

            summary_filename = f"{task_id}_summary.md"
            summary_path = str(TASKS_DIR / task_id / summary_filename)
            async with aiofiles.open(summary_path, "w", encoding="utf-8") as f:
                await f.write(summary_text)

            task.summary_filename = summary_filename
            task.updated_at = datetime.utcnow()
            db.commit()
            logger.info("任务 %s: 文本总结完成", task_id)
        except Exception as e:
            logger.error("任务 %s: 文本总结失败 - %s", task_id, str(e))
            task.status = TaskStatus.FAILED
            task.error_message = f"文本总结失败: {str(e)}"
            task.updated_at = datetime.utcnow()
            db.commit()
            return

        # ---- 步骤 3: 打包 ----
        try:
            logger.info("任务 %s: 开始打包", task_id)
            task.status = TaskStatus.PACKAGING
            task.updated_at = datetime.utcnow()
            db.commit()

            zip_filename = f"{task_id}.zip"
            package_task(
                task_dir=str(TASKS_DIR / task_id),
                audio_file=task.audio_filename,
                transcript_file=task.transcript_filename,
                summary_file=task.summary_filename,
                zip_filename=zip_filename,
            )

            task.zip_filename = zip_filename
            task.status = TaskStatus.DONE
            task.updated_at = datetime.utcnow()
            db.commit()
            logger.info("任务 %s: 打包完成，任务已完成", task_id)
        except Exception as e:
            logger.error("任务 %s: 打包失败 - %s", task_id, str(e))
            task.status = TaskStatus.FAILED
            task.error_message = f"打包失败: {str(e)}"
            task.updated_at = datetime.utcnow()
            db.commit()
            return

    except Exception as e:
        logger.error("任务 %s: 处理过程中发生未知错误 - %s", task_id, str(e))
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = TaskStatus.FAILED
                task.error_message = f"未知错误: {str(e)}"
                task.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            logger.error("任务 %s: 更新失败状态时出错", task_id)
    finally:
        db.close()


# ============ 路由端点 ============


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = FileParam(...),
    asr_mode: str = Form("file"),
    transcript_text: str = Form(None),
    db: Session = Depends(get_db),
):
    """上传录音并创建任务。

    Args:
        file: 音频文件。
        asr_mode: ASR 模式 - "realtime"（实时转写）或 "file"（文件转写）。
        transcript_text: 实时模式下前端已获得的转写文本（可选）。
    """
    # 校验 asr_mode
    if asr_mode not in ("realtime", "file"):
        raise HTTPException(status_code=400, detail=f"不支持的 asr_mode: {asr_mode}")

    # 校验文件类型（忽略 codecs 等参数，如 "audio/webm;codecs=opus" -> "audio/webm"）
    raw_content_type = file.content_type or ""
    base_content_type = raw_content_type.split(";")[0].strip().lower()
    if base_content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的音频格式: {file.content_type}。支持: {', '.join(ALLOWED_AUDIO_TYPES)}",
        )

    # 读取文件内容并校验大小
    content = await file.read()
    file_size_mb = len(content) / (1024 * 1024)
    if file_size_mb > MAX_AUDIO_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小 {file_size_mb:.1f}MB 超过限制 {MAX_AUDIO_SIZE_MB}MB",
        )

    # 生成任务 ID
    timestamp = int(time.time())
    short_uuid = uuid.uuid4().hex[:8]
    task_id = f"task_{timestamp}_{short_uuid}"

    # 创建任务目录
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # 保存音频文件
    audio_filename = file.filename or f"{task_id}_audio"
    audio_path = task_dir / audio_filename
    try:
        async with aiofiles.open(str(audio_path), "wb") as f:
            await f.write(content)
        logger.info("任务 %s: 音频文件已保存到 %s", task_id, audio_path)
    except Exception as e:
        logger.error("任务 %s: 保存音频文件失败 - %s", task_id, str(e))
        raise HTTPException(status_code=500, detail="保存音频文件失败")

    # 在数据库创建 Task 记录
    try:
        task = Task(
            id=task_id,
            status=TaskStatus.UPLOADED,
            audio_filename=audio_filename,
            task_dir=str(task_dir),
            asr_mode=asr_mode,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        logger.info("任务 %s: 数据库记录已创建 (asr_mode=%s)", task_id, asr_mode)
    except Exception as e:
        logger.error("任务 %s: 创建数据库记录失败 - %s", task_id, str(e))
        raise HTTPException(status_code=500, detail="创建任务记录失败")

    # 实时模式：转写文本已在前端通过 WebSocket 获得
    skip_transcribe = False
    if asr_mode == "realtime" and transcript_text:
        transcript_filename = f"{task_id}_transcript.txt"
        transcript_path = task_dir / transcript_filename
        try:
            async with aiofiles.open(str(transcript_path), "w", encoding="utf-8") as f:
                await f.write(transcript_text)
            task.transcript_filename = transcript_filename
            db.commit()
            skip_transcribe = True
            logger.info("任务 %s: 实时转写文本已保存 (%d 字符)", task_id, len(transcript_text))
        except Exception as e:
            logger.warning("任务 %s: 保存实时转写文本失败，将重新转写 - %s", task_id, str(e))

    # 启动后台异步处理
    background_tasks.add_task(process_task, task_id, skip_transcribe)

    return UploadResponse(
        task_id=task_id,
        status=task.status.value,
        message="音频上传成功，任务处理已启动",
    )


@router.get("/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, db: Session = Depends(get_db)):
    """查询任务状态"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return TaskStatusResponse(
        task_id=task.id,
        status=task.status.value,
        created_at=task.created_at,
        expires_at=task.expires_at,
        has_zip=task.zip_filename is not None,
    )


@router.get("/{task_id}/download")
async def download_zip(task_id: str, db: Session = Depends(get_db)):
    """下载任务 ZIP 文件"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status != TaskStatus.DONE:
        raise HTTPException(status_code=400, detail=f"任务尚未完成，当前状态: {task.status.value}")

    if task.is_expired:
        raise HTTPException(status_code=410, detail="任务已过期，文件已被清理")

    if not task.zip_filename:
        raise HTTPException(status_code=404, detail="ZIP 文件不存在")

    zip_path = TASKS_DIR / task_id / task.zip_filename
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP 文件未找到")

    return FileResponse(
        path=str(zip_path),
        filename=task.zip_filename,
        media_type="application/zip",
    )


@router.get("/{task_id}/audio")
async def serve_audio(task_id: str, db: Session = Depends(get_db)):
    """为 DashScope 文件转写提供音频文件访问。

    此端点供 DashScope 服务器回调拉取音频文件使用。
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if not task.audio_filename:
        raise HTTPException(status_code=404, detail="音频文件不存在")

    audio_path = TASKS_DIR / task_id / task.audio_filename
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="音频文件未找到")

    # 根据扩展名推断 MIME 类型
    suffix = audio_path.suffix.lower()
    media_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(audio_path),
        filename=task.audio_filename,
        media_type=media_type,
    )


# ============ 实时 ASR WebSocket 端点 ============


@router.websocket("/realtime-asr")
async def realtime_asr_websocket(ws: WebSocket):
    """实时语音转写 WebSocket 端点。

    协议:
    1. 客户端发送 JSON: {"action": "start", "sample_rate": 16000, "format": "pcm"}
    2. 客户端发送 binary 音频数据块
    3. 服务端返回部分转写结果 JSON: {"type": "partial", "text": "..."}
    4. 客户端发送 JSON: {"action": "stop"}
    5. 服务端返回最终结果 JSON: {"type": "final", "text": "..."}
    """
    await ws.accept()
    logger.info("实时 ASR WebSocket: 客户端已连接")

    from backend.services.asr_dashscope import RealtimeASRSession

    session = None
    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.receive":
                if "text" in message:
                    # JSON 控制消息
                    data = json.loads(message["text"])
                    action = data.get("action")

                    if action == "start":
                        sample_rate = data.get("sample_rate", 16000)
                        audio_format = data.get("format", "pcm")

                        session = RealtimeASRSession(
                            sample_rate=sample_rate,
                            audio_format=audio_format,
                        )

                        async def on_partial(text):
                            try:
                                await ws.send_json({"type": "partial", "text": text})
                            except Exception:
                                pass

                        await session.connect(on_partial=on_partial)
                        await ws.send_json({"type": "started"})
                        logger.info("实时 ASR WebSocket: 会话已启动")

                    elif action == "stop":
                        if session:
                            final_text = await session.finish()
                            await session.close()
                            session = None
                            await ws.send_json({
                                "type": "final",
                                "text": final_text,
                            })
                            logger.info("实时 ASR WebSocket: 返回最终结果 (%d 字符)", len(final_text))
                        break

                elif "bytes" in message:
                    # 二进制音频数据块
                    if session:
                        await session.send_audio_chunk(message["bytes"])

            elif message["type"] == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        logger.info("实时 ASR WebSocket: 客户端断开连接")
    except Exception as e:
        logger.error("实时 ASR WebSocket 异常: %s", str(e))
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if session:
            await session.close()


# ============ 任务列表（注意路由前缀为 /api/task，此处路径为 "s" 拼接为 /api/tasks） ============


@router.get("s", response_model=TaskListResponse)
async def list_tasks(db: Session = Depends(get_db)):
    """查询近 7 天任务列表（不包含 EXPIRED 状态）"""
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    tasks = (
        db.query(Task)
        .filter(
            Task.created_at >= seven_days_ago,
            Task.status != TaskStatus.EXPIRED,
        )
        .order_by(Task.created_at.desc())
        .all()
    )

    task_items = [
        TaskListItem(
            id=t.id,
            status=t.status.value,
            created_at=t.created_at,
            expires_at=t.expires_at,
            zip_filename=t.zip_filename,
        )
        for t in tasks
    ]

    return TaskListResponse(tasks=task_items, total=len(task_items))
