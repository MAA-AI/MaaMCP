import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
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


def _resize_short_edge(image, target_short_edge: int):
    """按短边归一化到 target_short_edge 像素，保留原图长宽比。

    设计：720p 不锁死 16:9。720p = 短边 720 像素。原始设备是 1080p / 1440p /
    非 16:9（如 16:10、5:4）等任意比例，都按短边等比缩放。

    示例（target=720）：
    - 1920×1080 (横屏 1080p) → 1280×720
    - 1080×1920 (竖屏 1080p) → 720×1280
    - 1280×800 (16:10 平板)   → 1152×720
    - 1280×1024 (5:4 显示器)  →  900×720
    - 1280×720 (已归一化)     → no-op

    Args:
        image: cv2 图像 (numpy.ndarray)。
        target_short_edge: 目标短边像素数，必须 > 0。

    Returns:
        缩放后的图像；若短边已等于 target，则原图返回（no-op）。
    """
    if target_short_edge <= 0:
        raise ValueError(
            f"target_short_edge 必须 > 0，实际: {target_short_edge}"
        )
    h, w = image.shape[:2]
    short = min(h, w)
    if short == target_short_edge:
        return image
    scale = target_short_edge / short
    if h <= w:
        new_h = target_short_edge
        new_w = int(round(w * scale))
    else:
        new_w = target_short_edge
        new_h = int(round(h * scale))
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _scale_region_to_image(
    region: Tuple[float, float, float, float],
    raw_shape: Tuple[int, int],
    new_shape: Tuple[int, int],
) -> Tuple[float, float, float, float]:
    """按比例把 region 从 raw 坐标空间缩放到 new 坐标空间。

    当 raw_shape == new_shape 时直接返回原 region（no-op）。

    Args:
        region: (x, y, w, h) 在 raw 坐标空间的区域。
        raw_shape: 原始图像 (H, W)。
        new_shape: 目标图像 (H, W)。

    Returns:
        缩放后的 (x, y, w, h)。
    """
    raw_h, raw_w = raw_shape
    new_h, new_w = new_shape
    if (raw_w, raw_h) == (new_w, new_h):
        return region
    sx = new_w / raw_w
    sy = new_h / raw_h
    x, y, w, h = region
    return (x * sx, y * sy, w * sx, h * sy)


def _apply_screencap_pipeline(
    raw,
    region: Optional[Tuple[int, int, int, int]],
    resolution: Optional[int],
):
    """screencap 图像处理流水线（纯函数，可独立单测）。

    流程：
    1. 若 resolution 非 None：按短边归一化
    2. 若 region 非 None 且做了归一化：按比例缩放 region（从 raw 坐标系 → new 坐标系）
    3. 按 region 裁剪（无 region 则返回全图）

    region 语义：坐标空间永远是 raw 设备原始分辨率。函数负责缩放。
    """
    raw_h, raw_w = raw.shape[:2]

    if resolution is not None:
        image = _resize_short_edge(raw, resolution)
    else:
        image = raw

    if region is not None and resolution is not None:
        new_h, new_w = image.shape[:2]
        region = _scale_region_to_image(
            region, raw_shape=(raw_h, raw_w), new_shape=(new_h, new_w)
        )

    return _crop_region(image, region)


def _screencap(
    controller_id: str,
    region: Optional[Tuple[int, int, int, int]] = None,
    resolution: Optional[int] = 720,
) -> Optional[str]:
    """截图核心实现：拉 controller 截图 + 应用图像处理流水线 + 落盘。

    region 语义（重要）：
        region 的 (x, y, w, h) **永远在设备原始分辨率坐标系**下，不在落盘图
        坐标系下。函数会按归一化比例自动缩放。AI 无需关心输出图实际是
        720p 还是其他尺寸。
    """
    controller: Controller | None = object_registry.get(controller_id)
    if not controller:
        return None
    raw = controller.post_screencap().wait().get()
    if raw is None:
        return None

    image = _apply_screencap_pipeline(raw, region, resolution)

    # 落盘
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
              ⚠️ region 坐标空间 = **设备原始分辨率**（不是落盘图分辨率）。
              若设备是 1080p (1920×1080)，region 的 x/y/w/h 都按 1080p 坐标想。
              函数会按归一化比例自动缩放 region，无需 AI 关心输出图实际尺寸。
              适用于"我只想看搜框附近 / 某个按钮周围"的场景，省传输与读图时间。
              不传则截全屏（默认行为）。
    - resolution: 可选整数，短边归一化目标（像素），默认 720。
              720p 不锁死 16:9：按短边等比缩放，原图长宽比保留。
              例如：1920×1080 → 1280×720；1080×1920 → 720×1280；
                    1280×800 (16:10) → 1152×720。
              传 None 跳过归一化，落盘图为原始设备分辨率（region 也在原始空间）。

    返回值：
    - 成功：返回截图文件的绝对路径，可通过 read_file 工具读取图片内容
    - 失败：返回 None

    多模态裁图工作流（你 M3 可用）：
    1. screencap(cid) → Read 源图 → 多模态识别目标元素（如按钮、图标、文字块）
    2. 推断目标元素 region (x, y, w, h)——按设备原始分辨率想
    3. screencap(cid, region=...) → 一步裁出
    4. Read 裁剪结果 → 视觉验证是否为目标元素
    5. 不满意就调 region 重试（UI 稳定时可多次迭代）
    6. 用 save_captured_image(captured_path, bundle_root, subcategory, name)
       把裁好的图存到项目 <bundle_root>/image/<子分类>/<元素名>.png。
       bundle_root 是传给 Resource.post_bundle() 的目录（如 MAAGC 是
       assets/resource/base/），路径与 TemplateMatch 的 template 字段读取路径一致。
""",
)
def screencap(
    controller_id: str,
    region: Optional[Tuple[int, int, int, int]] = None,
    resolution: Optional[int] = 720,
) -> Optional[str]:
    return _screencap(controller_id, region, resolution)


# ---------------------------------------------------------------------------
# save_captured_image: 把已裁好的截图存到 MaaFramework 项目 bundle 的 image 目录
# ---------------------------------------------------------------------------


def _validate_path_segment(value: str, field_name: str) -> str:
    """校验 subcategory / name 等路径段：非空、不含 ..、不是绝对路径。

    Args:
        value: 待校验的字符串。
        field_name: 用于错误信息的字段名。

    Returns:
        去除首尾空白后的字符串。

    Raises:
        ValueError: 校验失败，错误信息含字段名和原因。
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是 str，实际类型: {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} 不能为空")
    # 以路径分隔符开头视为可疑：在 POSIX 上是绝对路径，在 Windows 上
    # 是相对当前盘根的相对路径，但用户的意图大概率是绝对路径，统一拒绝
    if stripped.startswith(("/", "\\")):
        raise ValueError(f"{field_name} 不能以路径分隔符开头: {value!r}")
    # 资源路径可能在不同操作系统间共享，因此同时按 POSIX 和
    # Windows 语义校验，避免把 C:\foo 在 POSIX 上当成普通相对路径。
    posix_path = PurePosixPath(stripped)
    windows_path = PureWindowsPath(stripped)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise ValueError(f"{field_name} 不能是绝对路径: {value!r}")
    # C:foo 是 Windows 驱动器相对路径，虽不是绝对路径，也不应被
    # 作为可移植的 bundle 内部路径接受。
    if windows_path.drive:
        raise ValueError(f"{field_name} 不能包含 Windows 驱动器前缀: {value!r}")
    # 任何包含 .. 段的形式都拒绝（含 ../foo、foo/../bar、foo/..）
    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise ValueError(f"{field_name} 不能包含 '..' 段: {value!r}")
    return stripped


def _save_captured_image(
    captured_path: str,
    bundle_root: Union[str, Path],
    subcategory: str,
    name: str,
    overwrite: bool = False,
) -> str:
    """把已截好的 PNG 存到项目 MaaFramework bundle 的 image 目录。

    目标路径：<bundle_root>/image/<subcategory>/<name>.png。
    bundle_root 是传给 Resource.post_bundle() 的目录（与 MaaFramework
    TemplateMatch 的 template 字段读取路径一致；MAAGC 的 bundle_root
    是 assets/resource/base/）。

    Args:
        captured_path: screencap 返回的截图绝对路径。
        bundle_root: 项目 bundle 根目录；不存在会自动创建。
        subcategory: 模板分类子目录（如 UI/、Task/），可新建。
        name: 模板元素名（不含 .png 后缀）。
        overwrite: 目标已存在时是否覆盖；默认 False 保护已有模板。

    Returns:
        目标绝对路径字符串。

    Raises:
        ValueError: 参数校验失败（路径段非法）。
    """
    safe_subcategory = _validate_path_segment(subcategory, "subcategory")
    safe_name = _validate_path_segment(name, "name")

    src = Path(captured_path)
    if not src.is_file():
        raise ValueError(f"captured_path 不是有效文件: {captured_path}")

    bundle_root_path = Path(bundle_root).expanduser()
    target = bundle_root_path / "image" / safe_subcategory / f"{safe_name}.png"

    if target.exists() and not overwrite:
        raise ValueError(
            f"目标已存在且 overwrite=False: {target.absolute()}（如需覆盖请显式传 overwrite=True）"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(target))
    return str(target.absolute())


@mcp.tool(
    name="save_captured_image",
    description="""
    把已裁好的截图（通常是 screencap(cid, region=...) 返回的 PNG 路径）存到
    MaaFramework 项目 bundle 的 image 目录，作为 TemplateMatch 模板 / 节点测试素材。

    参数：
    - captured_path: 已存在的 PNG 文件绝对路径，通常是 screencap() 的返回值。
    - bundle_root: 项目 bundle 根目录（传给 Resource.post_bundle() 的目录）。
                   例如 MAAGC 用 "F:/workspace/MAAGC/assets/resource/base"；
                   MaaFramework sample 用 "<repo>/sample/resource"。
                   目录不存在会自动创建。
    - subcategory: image 目录下的分类子目录（可新建），如 "UI"、"Task"、"MyFeature"。
                   注意：这是路径段，禁止含 ".."、不能是绝对路径。
    - name: 模板元素名（不含 .png 后缀），如 "ConfirmButton"、"CloseIcon"。
                 同上：禁止含 ".."、不能是绝对路径。
    - overwrite: 目标文件已存在时是否覆盖。默认 False 以保护已有模板；
                 显式确认要覆盖时传 True。

    返回值：
    - 成功：返回写入的目标绝对路径字符串。
    - 失败：返回错误信息字符串（含失败原因）。

    典型工作流：
    1. screencap(cid) → Read → 多模态识别目标 → 推断 region
    2. screencap(cid, region=...) → 拿到 cropped 路径
    3. Read 裁图视觉验证是不是预期元素
    4. save_captured_image(cropped, bundle_root, subcategory, name)
    5. 在 pipeline JSON 用 "template": "<subcategory>/<name>.png" 引用
    6. （可选）benchmark_node() 跑 N 次验证阈值 / ROI

    路径约定：
    目标 = <bundle_root>/image/<subcategory>/<name>.png
    与 TemplateMatch 的 template 字段读取路径（<bundle_root>/image/...）一致。
""",
)
def save_captured_image(
    captured_path: str,
    bundle_root: Union[str, Path],
    subcategory: str,
    name: str,
    overwrite: bool = False,
) -> str:
    try:
        return _save_captured_image(
            captured_path, bundle_root, subcategory, name, overwrite
        )
    except ValueError as e:
        return f"参数错误: {e}"
    except OSError as e:
        return f"写入文件失败: {e}"
