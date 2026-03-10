"""
AI 语音任务处理系统 - 数据模型
"""
import enum
from datetime import datetime, timedelta
from sqlalchemy import Column, String, Integer, Text, DateTime, Enum as SAEnum
from backend.database import Base
from backend.config import RETENTION_DAYS


class TaskStatus(str, enum.Enum):
    """任务状态枚举"""
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    TRANSCRIBING = "TRANSCRIBING"
    SUMMARIZING = "SUMMARIZING"
    PACKAGING = "PACKAGING"
    DONE = "DONE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class Task(Base):
    """任务模型"""
    __tablename__ = "tasks"

    id = Column(String(64), primary_key=True, index=True)
    status = Column(SAEnum(TaskStatus), default=TaskStatus.CREATED, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    # 文件路径
    audio_filename = Column(String(256), nullable=True)
    transcript_filename = Column(String(256), nullable=True)
    summary_filename = Column(String(256), nullable=True)
    zip_filename = Column(String(256), nullable=True)

    # 任务目录
    task_dir = Column(String(512), nullable=True)

    # ASR 模式: "realtime" (实时转写) 或 "file" (文件转写)
    asr_mode = Column(String(32), nullable=True, default="file")

    # 错误信息
    error_message = Column(Text, nullable=True)

    def __init__(self, **kwargs):
        if "expires_at" not in kwargs:
            kwargs["expires_at"] = datetime.utcnow() + timedelta(days=RETENTION_DAYS)
        super().__init__(**kwargs)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at


class SystemConfig(Base):
    """系统配置模型"""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(128), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
