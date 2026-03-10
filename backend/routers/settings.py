"""
AI 语音任务处理系统 - 设置路由
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.config import DEFAULT_SUMMARY_PROMPT
from backend.database import get_db
from backend.models import SystemConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# ============ Pydantic 模型 ============


class SettingsResponse(BaseModel):
    summary_prompt: str


class SettingsUpdateRequest(BaseModel):
    summary_prompt: str = Field(..., min_length=1, description="总结提示词模板")


class SettingsUpdateResponse(BaseModel):
    message: str
    summary_prompt: str


# ============ 路由端点 ============


@router.get("", response_model=SettingsResponse)
async def get_settings(db: Session = Depends(get_db)):
    """获取系统设置"""
    try:
        config_row = (
            db.query(SystemConfig)
            .filter(SystemConfig.key == "summary_prompt")
            .first()
        )
        summary_prompt = config_row.value if config_row else DEFAULT_SUMMARY_PROMPT

        return SettingsResponse(summary_prompt=summary_prompt)
    except Exception as e:
        logger.error("获取系统设置失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取系统设置失败")


@router.put("", response_model=SettingsUpdateResponse)
async def update_settings(
    request: SettingsUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新系统设置"""
    try:
        config_row = (
            db.query(SystemConfig)
            .filter(SystemConfig.key == "summary_prompt")
            .first()
        )

        if config_row:
            config_row.value = request.summary_prompt
            config_row.updated_at = datetime.utcnow()
            logger.info("总结提示词已更新")
        else:
            config_row = SystemConfig(
                key="summary_prompt",
                value=request.summary_prompt,
            )
            db.add(config_row)
            logger.info("总结提示词已创建")

        db.commit()

        return SettingsUpdateResponse(
            message="设置更新成功",
            summary_prompt=request.summary_prompt,
        )
    except Exception as e:
        db.rollback()
        logger.error("更新系统设置失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新系统设置失败")
