from typing import Optional

from maa.toolkit import Toolkit
from maa.controller import AdbController

from maa_mcp.core import (
    mcp,
    object_registry,
    controller_info_registry,
    ControllerInfo,
    ControllerType,
)


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
    controller_id = object_registry.register(adb_controller)

    connection_params = {
        "adb_path": device.adb_path,
        "address": device.address,
        "screencap_methods": device.screencap_methods,
        "input_methods": device.input_methods,
        "config": device.config,
    }

    controller_info_registry[controller_id] = ControllerInfo(
        controller_type=ControllerType.ADB, connection_params=connection_params
    )
    return controller_id
