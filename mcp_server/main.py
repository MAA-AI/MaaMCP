import json
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
from maa.controller import AdbController, Win32Controller
from maa.resource import Resource
from maa.tasker import Tasker, TaskDetail
from maa.pipeline import JRecognitionType, JOCR

from mcp_server.registry import ObjectRegistry

object_registry = ObjectRegistry()
# 记录当前会话保存的截图文件路径，用于退出时清理
_saved_screenshots: list[Path] = []
# 记录当前加载的资源路径，用于 pipeline 管理
_current_resource_path: Optional[Path] = None

mcp = FastMCP(
    "MAA MCP",
    version="1.0.0",
    instructions="""
    MAA MCP 是一个基于 MaaFramewok 框架的 Model Context Protocol 服务，
    提供 Android 设备、Windows 桌面自动化控制能力，支持通过 ADB 连接模拟器或真机，通过窗口句柄连接Windows桌面
    实现屏幕截图、光学字符识别（OCR）、坐标点击、手势滑动、按键点击、输入文本等自动化操作。

    标准工作流程：
    1. ADB 设备列表发现与连接
       - 调用 find_adb_device_list() 扫描可用的 ADB 设备
       - 若发现多个设备，必须暂停执行并询问用户选择目标设备（禁止自动选择）
       - 使用 connect_adb_device(device_name) 建立设备连接，获取控制器 ID

    2. Windows 桌面列表发现与连接
       - 调用 find_window_list() 扫描可用的 Windows 桌面
       - 若发现多个桌面，必须暂停执行并询问用户选择目标桌面（禁止自动选择）
       - 使用 connect_window(window_name) 建立桌面连接，获取控制器 ID

    3. 资源初始化
       - 调用 load_resource(resource_path) 加载资源包
       - 资源路径应指向包含 resource/model/*.onnx 的目录（通常为项目 assets/resource 目录）
       - 加载前需验证路径存在性，缺失时提示用户配置资源文件

    4. 任务管理器创建
       - 调用 create_tasker(controller_id, resource_id) 创建任务管理器实例
       - 将控制器与资源绑定，获取任务管理器 ID

    5. 自动化执行
       - 方式一（推荐）：调用 run_pipeline(tasker_id, entry) 执行预定义的任务流水线
       - 方式二（手动）：
         - 调用 ocr(tasker_id) 进行屏幕截图并执行 OCR 识别
         - 根据识别结果调用 click() 或 swipe() 执行相应操作
         - 重复执行，直至任务完成

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
    if not adb_controller.post_connection().wait().succeeded:
        return None
    return object_registry.register(adb_controller)


@mcp.tool(
    name="connect_window",
    description="""
    建立与指定窗口的连接，获取窗口控制器实例。

    参数：
    - window_name: 窗口名称，需通过 find_window_list() 获取

    返回值：
    - 成功：返回窗口控制器 ID（字符串），用于后续所有窗口操作
    - 失败：返回 None
    
    说明：
    窗口控制器 ID 将用于后续的点击、滑动、截图等操作，请妥善保存。
    """,
)
def connect_window(window_name: str) -> Optional[str]:
    window: DesktopWindow | None = object_registry.get(window_name)
    if not window:
        return None

    window_controller = Win32Controller(
        window.hwnd,
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
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
    - 成功：返回资源 ID（字符串），用于创建任务管理器
    - 失败：返回 None（路径不存在或资源加载失败）

    前置检查：
    调用前应验证路径存在性，若路径不存在，需提示用户先配置资源文件。
""",
)
def load_resource(resource_path: str) -> Optional[str]:
    global _current_resource_path
    path = Path(resource_path)
    if not path.exists():
        return None
    resource = Resource()
    if not resource.post_bundle(resource_path).wait().succeeded:
        return None
    
    _current_resource_path = path
    return object_registry.register(resource)


@mcp.tool(
    name="create_tasker",
    description="""
    创建任务管理器实例，将控制器与资源进行绑定，用于执行自动化任务。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - resource_id: 资源 ID，由 load_resource() 返回

    返回值：
    - 成功：返回任务管理器 ID（字符串），用于后续 OCR 识别等操作
    - 失败：返回 None（控制器或资源无效，或绑定失败）

    说明：
    任务管理器是执行 OCR 识别等自动化操作的核心组件，需确保控制器和资源均已成功初始化。
""",
)
def create_tasker(controller_id: str, resource_id: str) -> Optional[str]:
    controller = object_registry.get(controller_id)
    resource = object_registry.get(resource_id)
    if not controller or not resource:
        return None
    tasker = Tasker()
    tasker.bind(resource, controller)
    if not tasker.inited:
        return None

    return object_registry.register(tasker)


@mcp.tool(
    name="run_pipeline",
    description="""
    运行指定的任务流水线。

    参数：
    - tasker_id: 任务管理器 ID，由 create_tasker() 返回
    - entry: 流水线入口节点名称（字符串），需在 pipeline.json 中定义
    - pipeline_override: 流水线覆盖配置（字典，可选），用于动态修改流水线参数，默认为 {}

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    流水线任务会自动执行一系列识别和操作，直到任务完成或失败。
    """,
)
def run_pipeline(tasker_id: str, entry: str, pipeline_override: dict = {}) -> bool:
    tasker: Tasker | None = object_registry.get(tasker_id)
    if not tasker:
        return False

    return tasker.post_task(entry, pipeline_override).wait().succeeded


@mcp.tool(
    name="list_pipelines",
    description="""
    列出当前加载资源中的所有可用流水线文件。

    返回值：
    - 成功：返回流水线文件名列表（如 ["MyTask.json", "Daily.json"]）
    - 失败：返回 None（未加载资源或目录不存在）

    说明：
    必须先调用 load_resource() 加载资源。
    """,
)
def list_pipelines() -> Optional[list[str]]:
    if not _current_resource_path:
        return None
    
    pipeline_dir = _current_resource_path / "pipeline"
    if not pipeline_dir.exists():
        return []
    
    return [f.name for f in pipeline_dir.glob("*.json")]


@mcp.tool(
    name="read_pipeline",
    description="""
    读取指定流水线文件的内容。

    参数：
    - filename: 流水线文件名（如 "MyTask.json"）

    返回值：
    - 成功：返回流水线文件的 JSON 内容（字典）
    - 失败：返回 None（文件不存在或读取失败）

    说明：
    用于查看现有流水线的具体实现，判断是否满足需求。
    """,
)
def read_pipeline(filename: str) -> Optional[dict]:
    if not _current_resource_path:
        return None
    
    filepath = _current_resource_path / "pipeline" / filename
    if not filepath.exists():
        return None
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@mcp.tool(
    name="save_pipeline",
    description="""
    保存或更新流水线文件。

    参数：
    - filename: 流水线文件名（如 "NewTask.json"），必须以 .json 结尾
    - content: 流水线内容（字典），符合 MaaFramework 流水线协议格式

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    重要提示：
    1. 保存后，若要立即生效，建议重新调用 load_resource() 和 create_tasker() 以确保新文件被正确加载。
    2. 请谨慎覆盖现有文件。
    """,
)
def save_pipeline(filename: str, content: dict) -> bool:
    if not _current_resource_path:
        return False
    
    if not filename.endswith(".json"):
        filename += ".json"
        
    pipeline_dir = _current_resource_path / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = pipeline_dir / filename
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


@mcp.tool(
    name="ocr",
    description="""
    对当前设备屏幕进行截图，并执行光学字符识别（OCR）处理。

    参数：
    - tasker_id: 任务管理器 ID，由 create_tasker() 返回

    返回值：
    - 成功：返回识别结果字符串，包含识别到的文字、坐标信息、置信度等结构化数据
    - 失败：返回 None（截图失败或 OCR 识别失败）

    说明：
    识别结果可用于后续的坐标定位和自动化决策，通常包含文本内容、边界框坐标、置信度评分等信息。
""",
)
def ocr(tasker_id: str) -> Optional[list]:
    tasker: Tasker | None = object_registry.get(tasker_id)
    if not tasker:
        return None

    image = tasker.controller.post_screencap().wait().get()
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
    在设备屏幕上执行单点点击操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - x: 目标点的 X 坐标（像素，整数）
    - y: 目标点的 Y 坐标（像素，整数）

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    说明：
    坐标系统以屏幕左上角为原点 (0, 0)，X 轴向右，Y 轴向下。
""",
)
def click(controller_id: str, x: int, y: int) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    return controller.post_click(x, y).wait().succeeded


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
    在设备屏幕上执行按键点击操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - key: 要点击的按键（虚拟按键码）

    返回值：
    - 成功：返回 True
    - 失败：返回 False
    """,
)
def click_key(controller_id: str, key: int) -> bool:
    controller = object_registry.get(controller_id)
    if not controller:
        return False
    return controller.post_click_key(key).wait().succeeded


@mcp.tool(
    name="scroll",
    description="""
    在设备屏幕上执行鼠标滚轮操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 返回
    - x: 滚动的 X 坐标（像素，整数）
    - y: 滚动的 Y 坐标（像素，整数）

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
