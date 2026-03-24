from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import cv2

from maa.controller import Controller
from maa.tasker import TaskDetail
from maa.pipeline import JRecognitionType, JOCR

from maa_mcp.core import mcp, object_registry, _saved_screenshots
from maa_mcp.resource import get_or_create_tasker
from maa_mcp.download import check_ocr_files_exist
from maa_mcp.paths import get_screenshots_dir


def _screencap(controller_id: str) -> Optional[str]:
    controller: Controller | None = object_registry.get(controller_id)
    if not controller:
        return None
    image = controller.post_screencap().wait().get()
    if image is None:
        return None
    
    # 保存截图到跨平台用户数据目录，返回路径供大模型按需读取
    screenshots_dir = get_screenshots_dir()
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filepath = screenshots_dir / f"screenshot_{timestamp}.png"
    success = cv2.imwrite(str(filepath), image)
    if not success:
        return None
    # 记录当前会话保存的截图文件路径，用于退出时清理
    _saved_screenshots.append(filepath)
    return str(filepath.absolute())

def _ocr_impl(controller_id: str) -> Optional[Union[list, str]]:
    """
    OCR 核心实现（可被其他模块复用）
    
    参数：
    - controller_id: 控制器 ID
    
    返回值：
    - 成功：返回识别结果列表
    - OCR 资源不存在：返回字符串提示信息
    - 失败：返回 None
    """
    # 先检查 OCR 资源是否存在，不存在则返回提示信息让 AI 主动调用下载
    if not check_ocr_files_exist():
        return "OCR 模型文件不存在，请先调用 check_and_download_ocr() 下载 OCR 资源后重试"

    controller: Controller | None = object_registry.get(controller_id)
    tasker = get_or_create_tasker(controller_id)
    if not controller or not tasker:
        return None

    image = controller.post_screencap().wait().get()
    info: TaskDetail | None = (
        tasker.post_recognition(JRecognitionType.OCR, JOCR(), image).wait().get()
    )
    if not info:
        return None
    return info.nodes[0].recognition.all_results


@mcp.tool(
    name="ocr",
    description="""
    对当前设备屏幕进行截图，并执行光学字符识别（OCR）处理。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回

    返回值：
    - 成功：返回识别结果列表，包含识别到的文字、坐标信息、置信度等结构化数据
    - OCR 资源不存在（首次使用）：返回字符串提示信息，需要调用 check_and_download_ocr() 下载资源后重试
    - 失败：返回 None（截图失败或 OCR 识别失败）

    说明：
    识别结果可用于后续的坐标定位和自动化决策，通常包含文本内容、边界框坐标、置信度评分等信息。
    首次使用时，如果 OCR 模型文件不存在，会返回提示信息，此时需要调用 check_and_download_ocr() 下载资源后再重试。
    下载完成后即可正常使用，后续调用无需再次下载。
""",
)
def ocr(controller_id: str) -> Optional[Union[list, str]]:
    return _ocr_impl(controller_id)


@mcp.tool(
    name="screencap",
    description="""
    对当前设备屏幕进行截图。
    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    返回值：
    - 成功：返回截图文件的绝对路径，可通过 read_file 工具读取图片内容
    - 失败：返回 None
    """,
)
def screencap(controller_id: str) -> Optional[str]:
    return _screencap(controller_id)
