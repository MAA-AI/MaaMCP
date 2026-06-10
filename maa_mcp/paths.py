"""
跨平台路径管理模块

使用 platformdirs 获取跨平台的用户数据目录，用于存储 OCR 模型、配置等资源文件。
- Windows: C:\\Users\\<user>\\AppData\\Local\\MaaMCP\\
- macOS: ~/Library/Application Support/MaaMCP/
- Linux: ~/.local/share/MaaMCP/
"""

from pathlib import Path
from typing import Union

from platformdirs import user_data_dir

# 应用名称，用于创建数据目录
APP_NAME = "MaaMCP"
APP_AUTHOR = "MaaXYZ"


def get_data_dir() -> Path:
    """
    获取应用数据目录

    Returns:
        跨平台的用户数据目录路径
    """
    return Path(user_data_dir(APP_NAME, APP_AUTHOR))


def get_resource_dir() -> Path:
    """
    获取资源目录路径

    Returns:
        资源目录路径 (data_dir/resource)
    """
    return get_data_dir() / "resource"


def get_model_dir() -> Path:
    """
    获取模型目录路径

    Returns:
        模型目录路径 (data_dir/resource/model)
    """
    return get_resource_dir() / "model"


def get_ocr_dir() -> Path:
    """
    获取 OCR 模型目录路径

    Returns:
        OCR 模型目录路径 (data_dir/resource/model/ocr)
    """
    return get_model_dir() / "ocr"


def get_screenshots_dir() -> Path:
    """
    获取截图目录路径

    Returns:
        截图目录路径 (data_dir/screenshots)
    """
    return get_data_dir() / "screenshots"


def get_logs_dir() -> Path:
    """
    获取日志目录路径

    Returns:
        日志目录路径 (data_dir/logs)
    """
    return get_data_dir() / "logs"


def ensure_dirs() -> None:
    """
    确保所有必要的目录存在
    """
    dirs = [
        get_resource_dir(),
        get_model_dir(),
        get_ocr_dir(),
        get_screenshots_dir(),
        get_logs_dir(),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def ensure_within_pipelines_dir(candidate: Union[str, Path]) -> Path:
    """将候选路径解析为绝对路径，并校验其位于 pipelines 目录内。

    用于防止 save_pipeline / load_pipeline 的 path traversal 漏洞（CWE-22）。
    解析时展开 ~、规范化 . 与 .. 段、解析符号链接（Python 3.6+ 默认行为）。

    行为：
    - 锚点目录为 get_data_dir() / "pipelines"，每次调用时重新解析（不缓存），
      以保证测试可 monkeypatch 数据目录。
    - 不创建目录：调用方负责确保 pipelines/ 存在。
    - 空字符串 / None 显式拒绝。

    Args:
        candidate: 待校验路径（str 或 Path）。

    Returns:
        解析后的绝对路径（已 resolve()，已展开 ~）。

    Raises:
        TypeError: candidate 为 None。
        ValueError: 路径为空、解析失败、或解析后不在 pipelines 目录内。
    """
    if candidate is None:
        raise TypeError("candidate 不能为 None")
    if isinstance(candidate, str) and candidate == "":
        raise ValueError("路径不能为空")
    if isinstance(candidate, Path) and str(candidate) == "":
        raise ValueError("路径不能为空")

    base = (get_data_dir() / "pipelines").resolve()
    candidate_path = Path(candidate).expanduser()
    # 相对路径一律视为相对于 pipelines/ 目录（更符合调用方直觉）
    if not candidate_path.is_absolute():
        candidate_path = base / candidate_path
    try:
        resolved = candidate_path.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"路径解析失败: {candidate}: {e}") from e

    if not resolved.is_relative_to(base):
        raise ValueError(
            f"路径越界: {candidate}（解析后: {resolved}）不在 pipelines 目录 {base} 内"
        )
    return resolved
