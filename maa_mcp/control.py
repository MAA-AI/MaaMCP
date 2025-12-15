import time
from typing import Union

from maa_mcp.core import mcp, object_registry, controller_info_registry, ControllerType


@mcp.tool(
    name="click",
    description="""
    在设备屏幕上执行单点点击操作，支持长按。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
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
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
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

    使用建议：
    - ADB（Android 设备/模拟器）场景下，如需滚动/翻页，优先使用 swipe()
    - Windows（桌面窗口）场景下，如需滚动列表/页面，优先使用 scroll()；仅在需要拖拽/滑动手势时使用 swipe()

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
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
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
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
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
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


@mcp.tool(name="keyboard_shortcut", description="""
    在设备屏幕上执行键盘快捷键操作。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - modifiers: 修饰键列表（虚拟按键码），支持多个修饰键
    - primary_key: 主键（虚拟按键码）
    - duration: 按键持续时间（毫秒），默认为 50；设置较大值可实现长按

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    常用按键值：
    修饰键（Windows Virtual-Key Codes）：
      - Left Shift: 160 (0xA0)
      - Left Ctrl: 162 (0xA2)
      - Left Alt: 164 (0xA4)
      - Left Windows: 91 (0x5B)

    注意：该方法仅对 Windows 窗口控制器，且在 Seize 控制方式下有效，其他控制方式不支持。
""")
def keyboard_shortcut(
    controller_id: str, modifiers: list[int], primary_key: int, duration: int = 50
) -> Union[bool, str]:
    controller = object_registry.get(controller_id)
    if not controller:
        return False

    # 检查控制器类型和配置
    info = controller_info_registry.get(controller_id)
    if info:
        if info.controller_type == ControllerType.ADB:
            return "keyboard_shortcut 不支持 ADB 控制器，该方法仅适用于 Windows 窗口控制器。请使用 click_key 进行单个按键操作。"
        if info.controller_type == ControllerType.WIN32 and info.keyboard_method != "Seize":
            return f"keyboard_shortcut 仅支持 Seize 键盘模式，当前为 {info.keyboard_method}。可对同一窗口调用 connect_window(keyboard_method='Seize') 获取新 controller_id，原 controller_id 仍可用于其他操作。"

    for modifier in modifiers:
        if not controller.post_key_down(modifier).wait().succeeded:
            return False

    if not controller.post_key_down(primary_key).wait().succeeded:
        return False
    time.sleep(duration / 1000.0)
    if not controller.post_key_up(primary_key).wait().succeeded:
        return False

    for modifier in modifiers:
        if not controller.post_key_up(modifier).wait().succeeded:
            return False
    return True


@mcp.tool(
    name="scroll",
    description="""
    在设备屏幕上执行鼠标滚轮操作。

    使用建议：
    - Windows（桌面窗口）场景下，如需滚动列表/页面，优先使用 scroll()
    - ADB（Android 设备/模拟器）不支持 scroll()，请使用 swipe() 进行滑动/滚动

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - x: 横向滚动增量（建议传入 120 的整数倍以获得最佳兼容性）
    - y: 纵向滚动增量（建议传入 120 的整数倍以获得最佳兼容性；一般来说，负数为向下滚动，正数为向上滚动）

    返回值：
    - 成功：返回 True
    - 失败：返回 False

    注意：该方法仅对 Windows 窗口控制有效，无法作用于 ADB。
    """,
)
def scroll(controller_id: str, x: int, y: int) -> Union[bool, str]:
    controller = object_registry.get(controller_id)
    if not controller:
        return False

    # 检查控制器类型
    info = controller_info_registry.get(controller_id)
    if info and info.controller_type == ControllerType.ADB:
        return "scroll 不支持 ADB 控制器，该方法仅适用于 Windows 窗口控制器。请使用 swipe 进行滑动操作。"

    return controller.post_scroll(x, y).wait().succeeded
