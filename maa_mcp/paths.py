"""
跨平台路径管理模块

使用 platformdirs 获取跨平台的用户数据目录，用于存储 OCR 模型、配置等资源文件。
- Windows: C:\\Users\\<user>\\AppData\\Local\\MaaMCP\\
- macOS: ~/Library/Application Support/MaaMCP/
- Linux: ~/.local/share/MaaMCP/
"""

from pathlib import Path

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
