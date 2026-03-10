"""
AI 语音任务处理系统 - 过期任务清理服务

定期扫描数据库中已过期的任务，删除关联文件并更新状态。
"""
import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

from backend.database import SessionLocal
from backend.models import Task, TaskStatus

logger = logging.getLogger(__name__)


def cleanup_expired_tasks() -> int:
    """清理所有已过期且状态为 DONE 的任务。

    扫描数据库，找出 expires_at 早于当前时间且状态为 DONE 的任务，
    删除其任务目录及所有文件，并将状态更新为 EXPIRED。

    Returns:
        本次清理的任务数量。
    """
    db = SessionLocal()
    cleaned_count = 0
    now = datetime.utcnow()

    try:
        # 查询过期且状态为 DONE 的任务
        expired_tasks = (
            db.query(Task)
            .filter(Task.expires_at < now, Task.status == TaskStatus.DONE)
            .all()
        )

        if not expired_tasks:
            logger.info("没有需要清理的过期任务")
            return 0

        logger.info("发现 %d 个过期任务需要清理", len(expired_tasks))

        for task in expired_tasks:
            task_id = task.id
            task_dir = task.task_dir

            logger.info("开始清理过期任务: %s", task_id)

            # 删除任务目录及其所有文件
            if task_dir:
                dir_path = Path(task_dir)
                if dir_path.exists():
                    try:
                        shutil.rmtree(dir_path)
                        logger.info("已删除任务目录: %s", task_dir)
                    except OSError as e:
                        logger.error(
                            "删除任务目录失败: %s, 原因: %s",
                            task_dir,
                            str(e),
                        )
                        # 即使目录删除失败，仍然标记为过期
                else:
                    logger.warning(
                        "任务目录不存在，跳过文件删除: %s",
                        task_dir,
                    )

            # 更新任务状态为 EXPIRED
            task.status = TaskStatus.EXPIRED
            task.updated_at = now
            cleaned_count += 1

            logger.info("任务已标记为过期: %s", task_id)

        db.commit()
        logger.info("过期任务清理完成，共清理 %d 个任务", cleaned_count)

    except Exception as e:
        db.rollback()
        logger.error("清理过期任务时发生错误: %s", str(e), exc_info=True)
        raise
    finally:
        db.close()

    return cleaned_count


async def schedule_cleanup(interval_hours: int = 24) -> None:
    """后台定时清理任务调度器。

    以固定时间间隔周期性地执行过期任务清理。
    此函数设计为作为 asyncio 后台任务运行，不会自行返回。

    Args:
        interval_hours: 清理间隔时间（小时），默认为 24 小时。
    """
    interval_seconds = interval_hours * 3600

    logger.info(
        "过期任务清理调度器已启动, 清理间隔: %d 小时",
        interval_hours,
    )

    while True:
        try:
            logger.info("开始执行定时清理任务...")
            cleaned = cleanup_expired_tasks()
            logger.info("定时清理完成, 本次清理 %d 个任务", cleaned)
        except Exception as e:
            logger.error(
                "定时清理任务执行失败: %s",
                str(e),
                exc_info=True,
            )

        logger.debug("下次清理将在 %d 小时后执行", interval_hours)
        await asyncio.sleep(interval_seconds)
