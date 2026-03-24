from typing import Optional

from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.toolkit import DesktopWindow, Toolkit
from maa.controller import Win32Controller

from maa_mcp.core import (
    mcp,
    object_registry,
    controller_info_registry,
    ControllerInfo,
    ControllerType,
)

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

    if not window_controller.post_connection().wait().succeeded:
        return None
    controller_id = object_registry.register(window_controller)

    connection_params = {
        "hwnd": window.hwnd,
        "screencap_method": screencap_method,
        "mouse_method": mouse_method,
        "keyboard_method": keyboard_method,
    }

    controller_info_registry[controller_id] = ControllerInfo(
        controller_type=ControllerType.WIN32,
        connection_params=connection_params,
        keyboard_method=keyboard_method,
    )
    return controller_id
