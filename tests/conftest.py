"""pytest 全局 fixtures。

目前只提供一个 maa_dbg_controller fixture：用全黑 PNG + MaaFramework 的
DbgController 模拟设备，让集成测试能在没有真实 ADB / Win32 设备的情况下
跑完整 Resource → Tasker → post_task 链路。

DbgController 的官方约定：
- 构造参数 read_path：图片目录（或单张图）
- 行为：连接时加载所有图，post_screencap 时按文件名顺序循环返回
- 没有 enum 类型，行为是内置的（不同于 MaaFramework C++ 侧的可选模式）

依赖：Maafw 发布的 MaaDbgControlUnit 原生库。该库在部分 maafw wheel 里没
打包（pyproject 只要求 maafw>=5.2.6，没强制带 debug controller）。fixture
在检测到原生库缺失时 skip，不让本地/CI 跑挂。
"""
import os
import platform
from pathlib import Path

import cv2
import numpy as np
import pytest
from maa import controller as maa_controller

from maa_mcp.core import object_registry


def _has_dbg_native_library() -> bool:
    """检查 MaaDbgControlUnit 原生库是否在本环境可用。"""
    maafw_dir = Path(maa_controller.__file__).parent
    bin_dir = maafw_dir / "bin"
    system = platform.system()
    if system == "Windows":
        return (bin_dir / "MaaDbgControlUnit.dll").is_file()
    if system == "Darwin":
        return (bin_dir / "libMaaDbgControlUnit.dylib").is_file()
    return (bin_dir / "libMaaDbgControlUnit.so").is_file()


@pytest.fixture
def maa_dbg_controller(tmp_path):
    """返回一个已连接、可 post_screencap 的 DbgController 注册 id。

    自动注册到 maa_mcp.core.object_registry，测试结束后清理。
    若 MaaDbgControlUnit 原生库缺失则整个 fixture skip。
    """
    if not _has_dbg_native_library():
        pytest.skip(
            "MaaDbgControlUnit 原生库未随当前 maafw wheel 打包；"
            "如需运行 pipeline 集成测试，请安装包含该库的 maafw 版本或单独拷贝 DLL。"
        )

    screens = tmp_path / "screens"
    screens.mkdir()
    # 写两张 1280x720 全黑 PNG，DbgController 会按文件名顺序循环
    for i in (1, 2):
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.imwrite(str(screens / f"{i:03d}.png"), img)

    ctrl = maa_controller.DbgController(str(screens))
    ctrl.post_connection().wait()
    cid = object_registry.register(ctrl)
    try:
        yield cid
    finally:
        try:
            object_registry.unregister(cid)
        except Exception:
            pass
