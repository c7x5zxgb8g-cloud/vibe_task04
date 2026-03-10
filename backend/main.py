"""
AI 语音任务处理系统 - FastAPI 主入口
"""
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import SERVER_HOST, SERVER_PORT
from backend.database import init_db
from backend.routers.task import router as task_router
from backend.routers.settings import router as settings_router
from backend.services.cleaner import schedule_cleanup

# ============ 路径常量 ============
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# ============ 创建 FastAPI 应用 ============
app = FastAPI(
    title="AI 语音任务处理系统",
    version="1.0.0",
)

# ============ CORS 中间件（开发模式：允许所有源） ============
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 注册路由 ============
app.include_router(task_router)
app.include_router(settings_router)


# ============ 根路径：返回前端页面 ============
@app.get("/")
async def root():
    """返回前端首页"""
    return FileResponse(FRONTEND_DIR / "index.html")


# ============ 挂载前端静态文件（放在路由注册之后，避免覆盖 API 路径） ============
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")


# ============ 启动事件 ============
@app.on_event("startup")
async def startup_event():
    """应用启动时执行：初始化数据库 & 启动后台清理任务"""
    # 初始化数据库
    init_db()
    # 启动后台定时清理任务
    asyncio.create_task(schedule_cleanup())


# ============ 主程序入口 ============
if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=True,
    )
