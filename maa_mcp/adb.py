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
    - target_short_side: 控制器截图短边，默认 720；可设为其他正整数。
      传 None 使用原始尺寸。OCR、动作和 Pipeline 均使用该控制器的截图坐标，
      非 720 模式需要匹配的模板及 ROI。

    返回值：
    - 成功：返回控制器 ID（字符串），用于后续所有设备操作
    - 失败：返回 None

    说明：
    控制器 ID 将用于后续的点击、滑动、截图等操作，请妥善保存。
""",
)
def connect_adb_device(
    device_name: str, target_short_side: Optional[int] = 720
) -> Optional[str]:
    if target_short_side is not None and (
        type(target_short_side) is not int or target_short_side <= 0
    ):
        raise ValueError("target_short_side must be a positive integer or None")
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
    if target_short_side is None:
        configured = adb_controller.set_screenshot_use_raw_size(True)
    else:
        configured = adb_controller.set_screenshot_target_short_side(target_short_side)
    if not configured:
        return None

    if not adb_controller.post_connection().wait().succeeded:
        return None
    controller_id = object_registry.register(adb_controller)

    connection_params = {
        "adb_path": device.adb_path,
        "address": device.address,
        "screencap_methods": device.screencap_methods,
        "input_methods": device.input_methods,
        "config": device.config,
        "target_short_side": target_short_side,
    }

    controller_info_registry[controller_id] = ControllerInfo(
        controller_type=ControllerType.ADB, connection_params=connection_params
    )
    return controller_id
