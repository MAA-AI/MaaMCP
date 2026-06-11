from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Union

import cv2

from maa.controller import Controller
from maa.tasker import TaskDetail
from maa.pipeline import JRecognitionType, JOCR

from maa_mcp.core import mcp, object_registry, _saved_screenshots
from maa_mcp.resource import get_or_create_tasker
from maa_mcp.download import check_ocr_files_exist
from maa_mcp.paths import get_screenshots_dir


def _crop_region(image, region: Optional[Tuple[int, int, int, int]]):
    """按 (x, y, w, h) 裁剪 cv2 图像，region 为 None 时返回原图。自动 clamp 到图像边界。"""
    if region is None:
        return image
    x, y, w, h = region
    H, W = image.shape[:2]
    x = max(0, min(int(x), W - 1))
    y = max(0, min(int(y), H - 1))
    w = max(1, min(int(w), W - x))
    h = max(1, min(int(h), H - y))
    return image[y : y + h, x : x + w]


def _screencap(
    controller_id: str, region: Optional[Tuple[int, int, int, int]] = None
) -> Optional[str]:
    controller: Controller | None = object_registry.get(controller_id)
    if not controller:
        return None
    image = controller.post_screencap().wait().get()
    if image is None:
        return None

    image = _crop_region(image, region)

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


def _ocr_impl(
    controller_id: str, region: Optional[Tuple[int, int, int, int]] = None
) -> Optional[Union[list, str]]:
    """
    OCR 核心实现（可被其他模块复用）

    参数：
    - controller_id: 控制器 ID
    - region: 可选 (x, y, w, h)，用 maafw JOCR 的 roi+roi_offset 内置机制做
              区域 OCR 并自动把返回 box 坐标补偿到原窗口坐标系。
              roi_offset = roi，所以返回坐标等价于"在未裁剪原图上的位置"，
              可以直接拿去 click()。

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
    if image is None:
        return None

    # 用 maafw 内置的 roi + roi_offset 做区域 OCR + 坐标补偿
    # roi_offset 与 roi 取相同值，返回的 box 坐标就是原窗口坐标系
    if region is not None:
        x, y, w, h = int(region[0]), int(region[1]), int(region[2]), int(region[3])
        ocr_param = JOCR(roi=(x, y, w, h), roi_offset=(x, y, w, h))
    else:
        ocr_param = JOCR()

    info: TaskDetail | None = (
        tasker.post_recognition(JRecognitionType.OCR, ocr_param, image).wait().get()
    )
    if not info or not info.nodes:
        return None
    return info.nodes[0].recognition.all_results


@mcp.tool(
    name="ocr",
    description="""
    对当前设备屏幕进行截图，并执行光学字符识别（OCR）处理。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - region: 可选 (x, y, w, h) 整型元组，指定屏幕上的一个矩形区域，只对该区域做 OCR。
              适用于"我只想看搜框 / 侧边栏 / 某个对话框"等局部场景。
              返回结果里的 box 坐标已自动加回 region 偏移，仍是原屏幕坐标系，直接拿去 click 即可。
              不传则对整屏 OCR（默认行为，较慢）。

    返回值：
    - 成功：返回识别结果列表，包含识别到的文字、坐标信息、置信度等结构化数据
    - OCR 资源不存在（首次使用）：返回字符串提示信息，需要调用 check_and_download_ocr() 下载资源后重试
    - 失败：返回 None（截图失败或 OCR 识别失败）

    使用建议（region 选不好的回退策略）：
    - 优先用小 region（speed 提升 4-8x，结果更聚焦）
    - 如果 region 返回空列表/目标找不到，**先扩大 region 再重试**（常见原因：目标文本被裁剪到边缘、字体太小被裁掉）
    - 如果扩大 region 还找不到，**回退到全屏 OCR**（不传 region）——目标可能在另一个位置
    - 调试时可同时跑 region 和全屏，对比两个结果集找差异

    说明：
    识别结果可用于后续的坐标定位和自动化决策，通常包含文本内容、边界框坐标、置信度评分等信息。
    首次使用时，如果 OCR 模型文件不存在，会返回提示信息，此时需要调用 check_and_download_ocr() 下载资源后再重试。
    下载完成后即可正常使用，后续调用无需再次下载。
""",
)
def ocr(
    controller_id: str, region: Optional[Tuple[int, int, int, int]] = None
) -> Optional[Union[list, str]]:
    return _ocr_impl(controller_id, region)


@mcp.tool(
    name="screencap",
    description="""
    对当前设备屏幕进行截图。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - region: 可选 (x, y, w, h) 整型元组，指定屏幕上的一个矩形区域，只截并保存该区域。
              适用于"我只想看搜框附近 / 某个按钮周围"的场景，省传输与读图时间。
              不传则截全屏（默认行为）。

    返回值：
    - 成功：返回截图文件的绝对路径，可通过 read_file 工具读取图片内容
    - 失败：返回 None
""",
)
def screencap(
    controller_id: str, region: Optional[Tuple[int, int, int, int]] = None
) -> Optional[str]:
    return _screencap(controller_id, region)
