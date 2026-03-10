"""
AI 语音任务处理系统 - ZIP 打包服务

将转写结果和音频文件打包为 ZIP 压缩包，并清理中间文件以节省空间。
"""
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def package_task(
    task_dir: str,
    audio_file: str,
    transcript_file: str,
    summary_file: str,
    zip_filename: str,
) -> str:
    """将任务产生的文件打包为 ZIP 压缩包。

    将音频文件、转写文本文件和总结文件打包到一个 ZIP 文件中，
    打包完成后删除原始中间文件以节省磁盘空间（保留 ZIP）。

    Args:
        task_dir: 任务目录的路径。
        audio_file: 音频文件名（相对于 task_dir）。
        transcript_file: 转写文本文件名（相对于 task_dir）。
        summary_file: 总结文件名（相对于 task_dir）。
        zip_filename: 输出的 ZIP 文件名（相对于 task_dir）。

    Returns:
        生成的 ZIP 文件的绝对路径。

    Raises:
        FileNotFoundError: 任务目录或必要文件不存在。
        RuntimeError: 打包过程中发生错误。
    """
    task_path = Path(task_dir)
    if not task_path.exists():
        logger.error("任务目录不存在: %s", task_dir)
        raise FileNotFoundError(f"任务目录不存在: {task_dir}")

    # 构建文件完整路径
    files_to_pack: list[tuple[Path, str]] = []
    for filename in (audio_file, transcript_file, summary_file):
        file_path = task_path / filename
        if not file_path.exists():
            logger.error("文件不存在，无法打包: %s", file_path)
            raise FileNotFoundError(f"文件不存在，无法打包: {file_path}")
        files_to_pack.append((file_path, filename))

    zip_path = task_path / zip_filename

    logger.info(
        "开始打包任务文件: %s, 包含 %d 个文件",
        zip_filename,
        len(files_to_pack),
    )

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path, arcname in files_to_pack:
                zf.write(file_path, arcname=arcname)
                logger.debug("已添加文件到 ZIP: %s", arcname)

    except (zipfile.BadZipFile, OSError) as e:
        logger.error("ZIP 打包失败: %s", str(e))
        # 清理可能生成的不完整 ZIP 文件
        if zip_path.exists():
            zip_path.unlink()
        raise RuntimeError(f"ZIP 打包失败: {e}") from e

    zip_size = zip_path.stat().st_size
    logger.info(
        "ZIP 打包完成: %s (大小: %d bytes)",
        zip_filename,
        zip_size,
    )

    # 删除原始中间文件以节省空间（保留 ZIP）
    for file_path, filename in files_to_pack:
        try:
            file_path.unlink()
            logger.debug("已删除中间文件: %s", filename)
        except OSError as e:
            logger.warning("删除中间文件失败 (非致命): %s, 原因: %s", filename, str(e))

    logger.info("中间文件清理完成，仅保留 ZIP: %s", zip_filename)

    return str(zip_path)
