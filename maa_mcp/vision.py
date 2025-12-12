from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from maa.controller import Controller
from maa.tasker import TaskDetail
from maa.pipeline import JRecognitionType, JOCR

from maa_mcp.core import mcp, object_registry, _saved_screenshots
from maa_mcp.resource import get_or_create_tasker


@mcp.tool(
    name="ocr",
    description="""
    对当前设备屏幕进行截图，并执行光学字符识别（OCR）处理。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - resource_id: 资源 ID，由 load_resource() 返回

    返回值：
    - 成功：返回识别结果字符串，包含识别到的文字、坐标信息、置信度等结构化数据
    - 失败：返回 None（截图失败或 OCR 识别失败）

    说明：
    识别结果可用于后续的坐标定位和自动化决策，通常包含文本内容、边界框坐标、置信度评分等信息。
""",
)
def ocr(controller_id: str, resource_id: str) -> Optional[list]:
    controller: Controller | None = object_registry.get(controller_id)
    tasker = get_or_create_tasker(controller_id, resource_id)
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
    name="screencap",
    description="""
    对当前设备屏幕进行截图。
    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    返回值：
    - 成功：返回截图文件的绝对路径，可通过 read_file 工具读取图片内容
    - 失败：返回 None
    """,
)
def screencap(controller_id: str) -> Optional[str]:
    controller = object_registry.get(controller_id)
    if not controller:
        return None
    image = controller.post_screencap().wait().get()
    if image is None:
        return None
    # 保存截图到文件，返回路径供大模型按需读取，避免 Base64 占用大量 context
    screenshots_dir = Path(__file__).parent / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filepath = screenshots_dir / f"screenshot_{timestamp}.png"
    success = cv2.imwrite(str(filepath), image)
    if not success:
        return None
    _saved_screenshots.append(filepath)
    return str(filepath.absolute())

