import atexit
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from maa.toolkit import Toolkit

from fastmcp import FastMCP

from maa_mcp import __version__
from maa_mcp.registry import ObjectRegistry
from maa_mcp.paths import get_data_dir, ensure_dirs


# 确保所有必要的目录存在并初始化 MaaFramework
ensure_dirs()
Toolkit.init_option(get_data_dir(), {"stdout_level": 0})


class ControllerType(Enum):
    """控制器类型枚举"""

    ADB = auto()
    WIN32 = auto()


@dataclass
class ControllerInfo:
    """控制器信息，用于记录控制器类型和配置"""

    controller_type: ControllerType
    # Win32 专用：键盘输入方式
    keyboard_method: Optional[str] = None


# 全局对象注册表
object_registry = ObjectRegistry()
# 控制器信息注册表：controller_id -> ControllerInfo
controller_info_registry: dict[str, ControllerInfo] = {}

# 记录当前会话保存的截图文件路径，用于退出时清理
_saved_screenshots: list[Path] = []

mcp = FastMCP(
    "MaaMCP",
    version=__version__,
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

    2. 自动化执行循环
       - 调用 ocr(controller_id) 对指定设备进行屏幕截图和 OCR 识别
       - 首次使用时，如果 OCR 模型文件不存在，ocr() 会返回提示信息，需要调用 check_and_download_ocr() 下载资源
       - 下载完成后即可正常使用 OCR 功能，后续调用无需再次下载
       - 根据识别结果调用 click()、double_click()、scroll()、swipe() 等执行相应操作
       - 所有操作通过 controller_id 指定目标设备/窗口
       - 可在多个设备间切换操作，实现协同自动化

    屏幕识别策略：
    - 优先使用 OCR：始终优先调用 ocr() 进行文字识别，OCR 返回结构化文本数据，token 消耗极低
    - 按需使用截图：仅当以下情况时，才调用 screencap() 获取截图，再通过 read_file 读取图片进行视觉识别：
      1. OCR 结果不足以做出决策（如需要识别图标、图像、颜色、布局等非文字信息）
      2. 反复 OCR + 操作后界面状态无预期变化，可能存在弹窗、遮挡或其他视觉异常需要人工判断
    - 图片识别会消耗大量 token，应尽量避免频繁调用

    滚动/翻页策略：
    - ADB（Android 设备/模拟器）：优先使用 swipe() 实现页面滚动/列表翻动（scroll() 不支持 ADB）
    - Windows（桌面窗口）：优先使用 scroll() 实现列表/页面滚动（更符合鼠标滚轮语义）；仅在需要“拖拽/滑动手势”时才使用 swipe()

    注意事项：
    - controller_id 为字符串类型，由系统自动生成并管理
    - 操作失败时函数返回 None 或 False，需进行错误处理
    - 多设备场景下必须等待用户明确选择，不得自动决策
    - 请妥善保存 controller_id，以便在多设备间切换操作

    Windows 窗口控制故障排除：
    若使用 connect_window() 连接窗口后出现异常，可尝试切换截图/输入方式（需重新连接）：

    截图异常（画面为空、纯黑、花屏等）：
      - 多尝试几次（2~3次）确认是否为偶发问题，不要一次失败就切换
      - 若持续异常，按优先级切换截图方式重新连接：
        FramePool → PrintWindow → GDI → DXGI_DesktopDup_Window → ScreenDC
      - 最后手段：DXGI_DesktopDup（截取整个桌面，触控坐标会不正确，仅用于排查问题）

    键鼠操作无响应（操作后界面无变化）：
      - 多尝试几次（2~3次）确认是否为偶发问题，不要一次失败就切换
      - 若持续无响应，按优先级切换输入方式重新连接：
        鼠标：PostMessage → PostMessageWithCursorPos → Seize
        键盘：PostMessage → Seize

    安全约束（重要）：
    - 所有 ADB、窗口句柄 相关操作必须且仅能通过本 MCP 提供的工具函数执行
    - 严禁在终端中直接执行 adb 命令（如 adb devices、adb shell 等）
    - 严禁在终端中直接执行窗口句柄相关命令（如 GetWindowText、GetWindowTextLength 等）
    - 严禁使用其他第三方库或方法与 ADB 设备或窗口句柄交互
    - 严禁绕过本 MCP 工具自行实现设备控制逻辑

    Pipeline 生成功能：
    当用户要求生成 Pipeline 时，可以将执行过的自动化操作转换为 MaaFramework Pipeline JSON 格式，
    以便后续直接运行而无需再次调用 AI。

    生成 Pipeline 的工作流程：
    1. 先执行完所需的自动化操作
    2. 调用 get_pipeline_protocol() 获取 Pipeline 协议文档
    3. 根据文档规范，将执行过的**有效操作**编写为 Pipeline JSON
    4. 调用 save_pipeline() 保存生成的 Pipeline
    5. 调用 run_pipeline() 验证 Pipeline 是否正常运行
    6. 根据运行结果迭代优化 Pipeline（详见下方"Pipeline 验证与迭代优化"）

    生成 Pipeline 的关键原则：
    - 只保留成功路径：如果操作过程中尝试了多条路径（如先进入 A 菜单没找到目标，
      返回后又进入 B 菜单才找到），只在 Pipeline 中保留最终成功的路径（B 菜单），
      不要包含失败的尝试（A 菜单的操作）
    - 优先使用 OCR 识别：使用文字匹配比坐标匹配更具鲁棒性
    - 合理设置识别区域：根据 OCR 结果的坐标设置 roi 可以提高识别效率
    - 节点命名清晰：使用描述性的中文节点名称

    Pipeline 验证与迭代优化（重要）：
    生成 Pipeline 后，应通过 run_pipeline() 验证其正确性，并根据结果进行迭代优化：

    1. **运行验证**
       - 调用 run_pipeline(controller_id, pipeline_path) 执行 Pipeline
       - 检查返回的 TaskDetail：
         - status 为 succeeded 表示成功
         - status 为 failed 表示失败，需要分析 nodes 中哪个节点出错

    2. **失败分析与修复**
       当 Pipeline 运行失败时：
       - 检查 TaskDetail.nodes 找到失败的节点
       - 分析失败原因（识别失败、坐标偏移、界面变化等）
       - 调用 load_pipeline() 读取现有 Pipeline
       - 修改 Pipeline 内容，常见优化手段：
         - 增加备选识别节点（在 next 列表中添加多个候选）
         - 放宽 OCR 匹配条件（使用正则表达式或部分匹配）
         - 调整 roi 识别区域
         - 增加 post_delay 等待时间
         - 添加中间状态检测节点
       - 调用 save_pipeline() 保存修改（指定原文件路径覆盖更新）

    3. **重新执行自动化**
       如果通过分析发现 Pipeline 逻辑本身需要调整（如操作路径有误），可以：
       - 重新执行一遍自动化循环
       - 将新的操作经验与之前的结合
       - 生成更完善的 Pipeline

    4. **迭代直到成功**
       - 反复执行"运行→分析→修复→运行"循环
       - 直到 Pipeline 稳定运行成功
       - 建议多次验证以确保鲁棒性
    """,
)


def cleanup_screenshots():
    """清理当前会话保存的临时截图文件"""
    for filepath in _saved_screenshots:
        filepath.unlink(missing_ok=True)
    _saved_screenshots.clear()


atexit.register(cleanup_screenshots)
