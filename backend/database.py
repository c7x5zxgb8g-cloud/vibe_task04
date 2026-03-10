"""
AI 语音任务处理系统 - 数据库模块 (SQLite)
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config import DATABASE_PATH

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表"""
    from backend.models import Task, SystemConfig  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # 安全的 schema 迁移：为已有表添加新字段
    import sqlite3
    try:
        conn = sqlite3.connect(str(DATABASE_PATH))
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE tasks ADD COLUMN asr_mode VARCHAR(32) DEFAULT 'file'")
        conn.commit()
        conn.close()
    except Exception:
        pass  # 字段已存在，忽略
