import atexit
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
from fastmcp import FastMCP

from maa.define import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
)
from maa.toolkit import DesktopWindow, Toolkit
from maa.controller import AdbController, Win32Controller, Controller
from maa.resource import Resource
from maa.tasker import Tasker, TaskDetail
from maa.pipeline import JRecognitionType, JOCR

from mcp_server.registry import ObjectRegistry

object_registry = ObjectRegistry()
# 记录当前会话保存的截图文件路径，用于退出时清理
_saved_screenshots: list[Path] = []

mcp = FastMCP(
    "MaaMCP",
    version="1.0.0",
    instructions="""
    MaaMCP 是一个基于 MaaFramewok 框架的 Model Context Protocol 服务，
    提供 Android 设备、Windows 桌面自动化控制能力，支持通过 ADB 连接模拟器或真机，通过窗口句柄连接 Windows 桌面，
    实现屏幕截图、光学字符识别（OCR）、坐标点击、手势滑动、按键点击、输入文本等自动化操作。

    ⭐ 多设备/多窗口协同支持：
    - 可同时连接多个 ADB 设备和/或多个 Windows 窗口
    - 每个设备/窗口拥有独立的控制器 ID（controller_id）
    - 通过在操作时指定不同的 controller_id 实现多设备协同自动化

    标准工作流程：
    1. 设备/窗口发现与连接
       - 调用 find_adb_device_list() 扫描可用的 ADB 设备
       - 调用 find_window_list() 扫描可用的 Windows 窗口
       - 若发现多个设备/窗口，需向用户展示列表并等待用户选择需要操作的目标
       - 使用 connect_adb_device(device_name) 或 connect_window(window_name) 建立连接
       - 可连接多个设备/窗口，每个连接返回独立的控制器 ID

    2. 资源初始化
       - 调用 load_resource(resource_path) 加载资源包（OCR 模型等）
       - 资源路径应指向包含 resource/model/*.onnx 的目录（通常为项目 assets/resource 目录）
       - 资源只需加载一次，可供多个设备共享使用

    3. 自动化执行循环
       - 调用 ocr(controller_id, resource_id) 对指定设备进行屏幕截图和 OCR 识别
       - 根据识别结果调用 click()、double_click()、swipe() 等执行相应操作
       - 所有操作通过 controller_id 指定目标设备/窗口
       - 可在多个设备间切换操作，实现协同自动化

    屏幕识别策略（重要）：
    - 优先使用 OCR：始终优先调用 ocr() 进行文字识别，OCR 返回结构化文本数据，token 消耗极低
    - 按需使用截图：仅当以下情况时，才调用 screencap() 获取截图，再通过 read_file 读取图片进行视觉识别：
      1. OCR 结果不足以做出决策（如需要识别图标、图像、颜色、布局等非文字信息）
      2. 反复 OCR + 操作后界面状态无预期变化，可能存在弹窗、遮挡或其他视觉异常需要人工判断
    - 图片识别会消耗大量 token，应尽量避免频繁调用

    注意事项：
    - 所有 ID 均为字符串类型，由系统自动生成并管理
    - 操作失败时函数返回 None 或 False，需进行错误处理
    - 多设备场景下必须等待用户明确选择，不得自动决策
    - 请妥善保存 controller_id 和 resource_id，以便在多设备间切换操作

    安全约束（重要）：
    - 所有 ADB、窗口句柄 相关操作必须且仅能通过本 MCP 提供的工具函数执行
    - 严禁在终端中直接执行 adb 命令（如 adb devices、adb shell 等）
    - 严禁在终端中直接执行窗口句柄相关命令（如 GetWindowText、GetWindowTextLength 等）
    - 严禁使用其他第三方库或方法与 ADB 设备或窗口句柄交互
    - 严禁绕过本 MCP 工具自行实现设备控制逻辑
    """,
)

Toolkit.init_option(Path(__file__).parent)


@mcp.tool(
    name="find_adb_device_list",
    description="""
    扫描并枚举当前系统中所有可用的 ADB 设备。

    返回值类型：
    - 设备名称列表

    重要约束：
    当返回多个设备时，必须立即暂停执行流程，向用户展示设备列表并等待用户明确选择。
    严禁在未获得用户确认的情况下自动选择设备。
""",
)
def find_adb_device_list() -> list[str]:
    device_list = Toolkit.find_adb_devices()
    for device in device_list:
        object_registry.register_by_name(device.name, device)

    return [device.name for device in device_list]


@mcp.tool(
    name="find_window_list",
    description="""
    扫描并枚举当前系统中所有可用的窗口。

    返回值类型：
    - 窗口名称列表

    重要约束：
    当返回多个窗口时，必须立即暂停执行流程，向用户展示窗口列表并等待用户明确选择。
    严禁在未获得用户确认的情况下自动选择窗口。
    """,
)
def find_window_list() -> list[str]:
    window_list = Toolkit.find_desktop_windows()
    for window in window_list:
        object_registry.register_by_name(window.window_name, window)
    return [window.window_name for window in window_list if window.window_name]


@mcp.tool(
    name="wait",
    description="""
    等待指定的时间（秒）。
    当需要等待界面加载、动画完成或操作生效时，以及其他需要等待的情况下使用。
    注意：由于客户端超时限制，单次等待最长支持 60 秒。如果需要等待更长时间，请多次调用。
    """,
)
def wait(seconds: float) -> str:
    max_wait = 60.0
    if seconds > max_wait:
        time.sleep(max_wait)
        return f"已等待 {max_wait} 秒（单次最大限制）。请再次调用 wait 以继续等待剩余时间。"

    time.sleep(seconds)
    return f"已等待 {seconds} 秒"


@mcp.tool(
    name="connect_adb_device",
    description="""
    建立与指定 ADB 设备的连接，创建控制器实例。

    参数：
    - device_name: 目标设备名称，需通过 find_adb_device_list() 获取

    返回值：
    - 成功：返回控制器 ID（字符串），用于后续所有设备操作
    - 失败：返回 None

    说明：
    控制器 ID 将用于后续的点击、滑动、截图等操作，请妥善保存。
""",
)
def connect_adb_device(device_name: str) -> Optional[str]:
    device = object_registry.get(device_name)
    if not device:
        return None

    adb_controller = AdbController(
        device.adb_path,
        device.address,
        device.screencap_methods,
        device.input_methods,
        device.config,
    )
    # 设置默认截图短边为 720p
    # 手机上文字/图标通常较大，不需要太高清
    adb_controller.set_screenshot_target_short_side(720)

    if not adb_controller.post_connection().wait().succeeded:
        return None
    return object_registry.register(adb_controller)


# 截图/鼠标/键盘方法名称到枚举值的映射
_SCREENCAP_METHOD_MAP = {
    "FramePool": MaaWin32ScreencapMethodEnum.FramePool,
    "PrintWindow": MaaWin32ScreencapMethodEnum.PrintWindow,
    "GDI": MaaWin32ScreencapMethodEnum.GDI,
    "DXGI_DesktopDup_Window": MaaWin32ScreencapMethodEnum.DXGI_DesktopDup_Window,
    "ScreenDC": MaaWin32ScreencapMethodEnum.ScreenDC,
    "DXGI_DesktopDup": MaaWin32ScreencapMethodEnum.DXGI_DesktopDup,
}

_MOUSE_METHOD_MAP = {
    "PostMessage": MaaWin32InputMethodEnum.PostMessage,
    "PostMessageWithCursorPos": MaaWin32InputMethodEnum.PostMessageWithCursorPos,
    "Seize": MaaWin32InputMethodEnum.Seize,
}

_KEYBOARD_METHOD_MAP = {
    "PostMessage": MaaWin32InputMethodEnum.PostMessage,
    "Seize": MaaWin32InputMethodEnum.Seize,
}


@mcp.tool(
    name="connect_window",
    description="""
    建立与指定窗口的连接，获取窗口控制器实例。

    参数：
    - window_name: 窗口名称，需通过 find_window_list() 获取
    - screencap_method: 截图方式，默认 "FramePool"（一般无需修改）
    - mouse_method: 鼠标输入方式，默认 "PostMessage"（一般无需修改）
    - keyboard_method: 键盘输入方式，默认 "PostMessage"（一般无需修改）

    返回值：
    - 成功：返回窗口控制器 ID（字符串），用于后续所有窗口操作
    - 失败：返回 None

    说明：
    窗口控制器 ID 将用于后续的点击、滑动、截图等操作，请妥善保存。

    截图/输入方式选择（仅当默认方式不工作时尝试切换）：

    截图方式优先级（从高到低）：
      - "FramePool"（默认，可后台）
      - "PrintWindow"（可后台）
      - "GDI"（可后台）
      - "DXGI_DesktopDup_Window"（只能前台）
      - "ScreenDC"（只能前台）
      - "DXGI_DesktopDup"（仅作最后手段！截取整个桌面而非单窗口，触控坐标会不正确）

    鼠标方式优先级（从高到低）：
      - "PostMessage"（默认，可后台）
      - "PostMessageWithCursorPos"（可后台，但偶尔会抢鼠标）
      - "Seize"（只能前台，会抢占鼠标键盘）

    键盘方式优先级（从高到低）：
      - "PostMessage"（默认，可后台）
      - "Seize"（只能前台，会抢占鼠标键盘）
    """,
)
def connect_window(
    window_name: str,
    screencap_method: str = "FramePool",
    mouse_method: str = "PostMessage",
    keyboard_method: str = "PostMessage",
) -> Optional[str]:
    window: DesktopWindow | None = object_registry.get(window_name)
    if not window:
        return None

    screencap_enum = _SCREENCAP_METHOD_MAP.get(
        screencap_method, MaaWin32ScreencapMethodEnum.FramePool
    )
    mouse_enum = _MOUSE_METHOD_MAP.get(
        mouse_method, MaaWin32InputMethodEnum.PostMessage
    )
    keyboard_enum = _KEYBOARD_METHOD_MAP.get(
        keyboard_method, MaaWin32InputMethodEnum.PostMessage
    )

    window_controller = Win32Controller(
        window.hwnd,
        screencap_method=screencap_enum,
        mouse_method=mouse_enum,
        keyboard_method=keyboard_enum,
    )
    # 设置默认截图短边为 1080p
    # 电脑屏幕通常较大，使用更高清的截图
    window_controller.set_screenshot_target_short_side(1080)

    # 或 使用原始尺寸截图，不进行缩放
    # window_controller.set_screenshot_use_raw_size(True)

    if not window_controller.post_connection().wait().succeeded:
        return None
    return object_registry.register(window_controller)


@mcp.tool(
    name="load_resource",
    description="""
    加载 MAA 资源包，包含 OCR 模型、图像模板等自动化所需资源。

    参数：
    - resource_path: 资源包根目录路径（字符串）
      - 路径应指向包含 resource/model/*.onnx 的目录层级
      - 典型路径示例：项目根目录下的 assets/resource
      - 传入路径为 resource 这一级目录，而非其子目录

    返回值：
    - 成功：返回资源 ID（字符串），用于后续 OCR 等操作
    - 失败：返回 None（路径不存在或资源加载失败）

    前置检查：
    调用前应验证路径存在性，若路径不存在，需提示用户先配置资源文件。
""",
)
def load_resource(resource_path: str) -> Optional[str]:
    if not Path(resource_path).exists():
        return None
    resource = Resource()
    if not resource.post_bundle(resource_path).wait().succeeded:
        return None
    return object_registry.register(resource)


def _get_or_create_tasker(resource_id: str) -> Optional[Tasker]:
    """
    根据 resource_id 获取或创建 tasker 实例。
    tasker 会被缓存，相同 resource_id 不会重复创建。
    """
    tasker_cache_key = f"_tasker_{resource_id}"
    tasker: Tasker | None = object_registry.get(tasker_cache_key)
    if tasker:
        return tasker

    resource: Resource | None = object_registry.get(resource_id)
    if not resource:
        return None

    tasker = Tasker()
    tasker.bind(resource, Controller())
    if not tasker.inited:
        return None

    object_registry.register_by_name(tasker_cache_key, tasker)
    return tasker


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
    controller = object_registry.get(controller_id)
    tasker = _get_or_create_tasker(resource_id)
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


@mcp.tool(
    name="click",
    description="""
    在设备屏幕上执行单点点击操作，支持长按。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - x: 目标点的 X 坐标（像素，整数）
    - y: 目标点的 Y 坐标（像素，整数）
    - button: 按键编号，默认为 0
      - ADB 控制器：手指编号（0 为第一根手指）
      - Win32 控制器：鼠标按键（0=左键, 1=右键, 2=中键）
    - duration: 按下持续时间（毫秒），默认为 50；设置较大值可实现长按

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    坐标系统以屏幕左上角为原点 (0, 0)，X 轴向右，Y 轴向下。
""",
)
def click(
    controller_id: str, x: int, y: int, button: int = 0, duration: int = 50
) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    if not controller.post_touch_down(x, y, contact=button).wait().succeeded:
        return False
    time.sleep(duration / 1000.0)
    return controller.post_touch_up(contact=button).wait().succeeded


@mcp.tool(
    name="double_click",
    description="""
    在设备屏幕上执行双击操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - x: 目标点的 X 坐标（像素，整数）
    - y: 目标点的 Y 坐标（像素，整数）
    - button: 按键编号，默认为 0
      - ADB 控制器：手指编号（0 为第一根手指）
      - Win32 控制器：鼠标按键（0=左键, 1=右键, 2=中键）
    - duration: 每次按下的持续时间（毫秒），默认为 50
    - interval: 两次点击之间的间隔时间（毫秒），默认为 100

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    坐标系统以屏幕左上角为原点 (0, 0)，X 轴向右，Y 轴向下。
""",
)
def double_click(
    controller_id: str,
    x: int,
    y: int,
    button: int = 0,
    duration: int = 50,
    interval: int = 100,
) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    # 第一次点击
    if not controller.post_touch_down(x, y, contact=button).wait().succeeded:
        return False
    time.sleep(duration / 1000.0)
    if not controller.post_touch_up(contact=button).wait().succeeded:
        return False
    # 间隔等待
    time.sleep(interval / 1000.0)
    # 第二次点击
    if not controller.post_touch_down(x, y, contact=button).wait().succeeded:
        return False
    time.sleep(duration / 1000.0)
    return controller.post_touch_up(contact=button).wait().succeeded


@mcp.tool(
    name="swipe",
    description="""
    在设备屏幕上执行手势滑动操作，模拟手指从起始点滑动到终点。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - start_x: 起始点的 X 坐标（像素，整数）
    - start_y: 起始点的 Y 坐标（像素，整数）
    - end_x: 终点的 X 坐标（像素，整数）
    - end_y: 终点的 Y 坐标（像素，整数）
    - duration: 滑动持续时间（毫秒，整数）

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    坐标系统以屏幕左上角为原点 (0, 0)。duration 参数控制滑动速度，数值越大滑动越慢。
""",
)
def swipe(
    controller_id: str,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: int,
) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    return (
        controller.post_swipe(start_x, start_y, end_x, end_y, duration).wait().succeeded
    )


@mcp.tool(
    name="input_text",
    description="""
    在设备屏幕上执行输入文本操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - text: 要输入的文本（字符串）

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    输入文本操作将模拟用户在设备屏幕上输入文本，支持中文、英文等常见字符。
    """,
)
def input_text(controller_id: str, text: str) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    return controller.post_input_text(text).wait().succeeded


@mcp.tool(
    name="click_key",
    description="""
    在设备屏幕上执行按键点击操作，支持长按。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - key: 要点击的按键（虚拟按键码）
    - duration: 按键持续时间（毫秒），默认为 50；设置较大值可实现长按

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    常用按键值：
    ADB 控制器（Android KeyEvent）：
      - 返回键: 4
      - Home键: 3
      - 菜单键: 82
      - 回车/确认: 66
      - 删除/退格: 67
      - 音量+: 24
      - 音量-: 25
      - 电源键: 26

    Win32 控制器（Windows Virtual-Key Codes）：
      - 回车: 13 (0x0D)
      - ESC: 27 (0x1B)
      - 退格: 8 (0x08)
      - Tab: 9 (0x09)
      - 空格: 32 (0x20)
      - 左箭头: 37 (0x25)
      - 上箭头: 38 (0x26)
      - 右箭头: 39 (0x27)
      - 下箭头: 40 (0x28)
    """,
)
def click_key(controller_id: str, key: int, duration: int = 50) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    if not controller.post_key_down(key).wait().succeeded:
        return False
    time.sleep(duration / 1000.0)
    return controller.post_key_up(key).wait().succeeded


@mcp.tool(
    name="scroll",
    description="""
    在设备屏幕上执行鼠标滚轮操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - x: 滚动的 X 坐标（像素，建议传入 120 的整数倍以获得最佳兼容性）
    - y: 滚动的 Y 坐标（像素，建议传入 120 的整数倍以获得最佳兼容性）

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    注意：该方法仅对 Windows 窗口控制有效，无法作用于 ADB。
    """,
)
def scroll(controller_id: str, x: int, y: int) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    return controller.post_scroll(x, y).wait().succeeded


def cleanup_screenshots():
    """清理当前会话保存的临时截图文件"""
    for filepath in _saved_screenshots:
        filepath.unlink(missing_ok=True)
    _saved_screenshots.clear()


atexit.register(cleanup_screenshots)
