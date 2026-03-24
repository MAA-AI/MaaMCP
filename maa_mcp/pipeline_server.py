# pipeline_server.py
"""
多线程流水线 MCP 服务器
======================
真正可运行的 MCP 服务器入口，支持多线程后台监控。

使用方法：
作为 MCP 服务器运行 (替代 __main__.py):
   python maa_mcp/pipeline_server.py
"""

import time
from threading import Thread, Event
from queue import Queue, Empty, Full
from typing import List, Dict, Any

# 导入 MCP Core 和 Registry
from maa_mcp.core import mcp, controller_info_registry, object_registry

# 导入功能模块以注册基础工具
import maa_mcp.adb
import maa_mcp.win32
import maa_mcp.vision
import maa_mcp.control
import maa_mcp.utils
import maa_mcp.resource

# 导入 Pipeline 子模块
from dataclasses import dataclass

from maa_mcp.pipeline import (
    setup_logger,
    get_logger,
    PipelineState,
    get_pipeline_state,
)


# 流水线配置类
@dataclass
class PipelineConfig:
    """流水线配置"""

    screenshot_fps: float = 2.0  # 截图帧率
    message_queue_size: int = 100  # 消息队列大小
    similarity_threshold: int = 5  # 图像相似度阈值
    enable_dedup: bool = True  # 启用消息去重


# UI 元素过滤列表（用于消息去重时过滤 UI 文本）
UI_ELEMENTS_FILTER = {"微信", "发送", "输入", "语音", "表情", "更多"}

# 导入现有的工具实现函数（内部函数，可直接调用）
from maa_mcp.vision import _ocr_impl

# ==================== 初始化日志 ====================

setup_logger()
logger = get_logger("PipelineServer")


# ==================== 流水线核心逻辑 ====================


def run_pipeline_loop(
    controller_id: str,
    config_dict: Dict,
    stop_event: Event,
    message_queue: Queue,
):
    """
    流水线主循环（多线程版）

    后台线程持续截图并执行 OCR，将 OCR 文字结果传递给大模型。
    大模型直接使用文字结果进行决策，无需处理图片。

    Args:
        controller_id: 控制器 ID
        config_dict: 配置字典
        stop_event: 停止事件
        message_queue: 消息队列（存放 OCR 结果）
    """
    thread_logger = get_logger("PipelineLoop")

    thread_logger.debug(f"[初始化] 流水线线程启动")
    thread_logger.debug(f"[初始化] controller_id={controller_id}")
    thread_logger.info(f"流水线线程启动，控制器: {controller_id}")

    fps = config_dict.get("fps", 2.0)
    frame_count = 0
    interval = 1.0 / fps

    thread_logger.debug(f"[初始化] fps={fps}, interval={interval}s")
    thread_logger.info("流水线初始化完成，开始主循环（OCR 模式）")

    while not stop_event.is_set():
        try:
            loop_start = time.time()
            frame_count += 1

            thread_logger.debug(f"[Frame {frame_count}] 开始 OCR...")

            # 调用 vision.py 中的 _ocr_impl，执行截图+OCR
            ocr_results = _ocr_impl(controller_id)

            # 处理 OCR 返回值
            if ocr_results is None:
                thread_logger.debug(f"[Frame {frame_count}] OCR 失败: None")
                time.sleep(interval)
                continue

            # 检查是否为错误信息（字符串）
            if isinstance(ocr_results, str):
                thread_logger.warning(f"[Frame {frame_count}] OCR 错误: {ocr_results}")
                time.sleep(interval)
                continue

            thread_logger.debug(f"[Frame {frame_count}] OCR 成功，结果条数: {len(ocr_results)}")

            # 将 OCR 结果放入消息队列
            message_data = {
                "type": "ocr",
                "ocr_results": ocr_results,
                "timestamp": time.time(),
                "frame_id": frame_count,
            }
            try:
                message_queue.put_nowait(message_data)
                thread_logger.info(f"📷 OCR 结果: {len(ocr_results)} 条")
            except Full:
                thread_logger.warning(f"[Frame {frame_count}] 消息队列已满，丢弃 OCR 结果")

            elapsed = time.time() - loop_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        except Exception as e:
            thread_logger.error(f"流水线异常: {e}")
            import traceback

            thread_logger.debug(f"堆栈: {traceback.format_exc()}")
            time.sleep(1)

    thread_logger.info("流水线线程已停止")


# ==================== MCP 工具实现 ====================


def _start_pipeline_impl(controller_id: str, fps: float = 2.0) -> str:
    """启动流水线实现"""
    try:
        logger.debug(
            f"[启动] 收到启动流水线请求: controller_id={controller_id}, fps={fps}"
        )

        pipeline_state = get_pipeline_state()

        if pipeline_state.is_running:
            return "⚠️ 流水线已经在运行中"

        # 获取控制器信息，验证 controller_id 是否有效
        info = controller_info_registry.get(controller_id)
        if not info:
            return f"❌ 未找到控制器: {controller_id}，请先连接设备"

        # 验证 controller 对象存在
        if object_registry.get(controller_id) is None:
            return f"❌ 未找到控制器对象: {controller_id}"

        pipeline_state.reset()
        pipeline_state.controller_id = controller_id
        pipeline_state.stats_dict["start_time"] = time.time()

        logger.info(f"正在启动流水线线程, controller_id={controller_id}")

        # 启动流水线线程，只需要传递 controller_id
        pipeline_state.pipeline_thread = Thread(
            target=run_pipeline_loop,
            args=(
                controller_id,
                {"fps": fps, "enable_dedup": True},
                pipeline_state.stop_event,
                pipeline_state.message_queue,
            ),
            daemon=True,
            name=f"PipelineThread-{controller_id}",
        )

        pipeline_state.pipeline_thread.start()
        pipeline_state.is_running = True

        logger.info(f"流水线已启动, Thread={pipeline_state.pipeline_thread.name}")

        return f"✅ 流水线已启动 (Thread: {pipeline_state.pipeline_thread.name})"
    except Exception as e:
        logger.exception("启动流水线失败")
        return f"❌ 启动流水线失败: {str(e)}"


def _stop_pipeline_impl() -> str:
    """停止流水线实现"""
    pipeline_state = get_pipeline_state()
    if not pipeline_state.is_running:
        return "⚠️ 流水线未在运行"

    pipeline_state.stop_event.set()
    if pipeline_state.pipeline_thread:
        pipeline_state.pipeline_thread.join(timeout=5)
        if pipeline_state.pipeline_thread.is_alive():
            logger.warning("流水线线程未能在5秒内停止")

    pipeline_state.is_running = False
    return "✅ 流水线已停止"


def _get_new_messages_impl(max_count: int = 10) -> List[Dict[str, Any]]:
    """获取消息实现"""
    pipeline_state = get_pipeline_state()
    messages = []
    for _ in range(max_count):
        try:
            messages.append(pipeline_state.message_queue.get_nowait())
        except Empty:
            break
    return messages


def _get_pipeline_status_impl() -> Dict[str, Any]:
    """获取状态实现"""
    pipeline_state = get_pipeline_state()
    stats = pipeline_state.get_stats()
    start_time = stats.get("start_time", 0)
    uptime = time.time() - start_time if start_time > 0 else 0
    return {
        "is_running": pipeline_state.is_running,
        "controller_id": pipeline_state.controller_id,
        "uptime": round(uptime, 1),
        "pending": pipeline_state.message_queue.qsize(),
    }


# ==================== MCP 工具注册 ====================


@mcp.tool(
    name="start_pipeline",
    description="""
    启动后台监控流水线，持续对设备屏幕进行截图+OCR 并缓存 OCR 结果。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - fps: 截图帧率（默认 2.0），控制每秒 OCR 次数

    返回值：
    - 成功：返回包含 "✅" 的成功信息
    - 失败：返回包含 "❌" 的错误信息

    说明：
    流水线启动后会在后台线程持续运行，定期截图并执行 OCR，将 OCR 结果放入消息队列。
    可通过 get_new_messages() 获取 OCR 结果，大模型直接使用文字结果进行决策。
    同一时间只能运行一个流水线实例。
    """,
)
def start_pipeline(controller_id: str, fps: float = 2.0) -> str:
    return _start_pipeline_impl(controller_id, fps)


@mcp.tool(
    name="stop_pipeline",
    description="""
    停止当前运行的后台监控流水线。

    参数：
    无

    返回值：
    - 成功：返回包含 "✅" 的成功信息
    - 未运行：返回包含 "⚠️" 的提示信息

    说明：
    停止流水线后，后台线程将结束运行，消息队列中的未读消息仍可通过 get_new_messages() 获取。
    """,
)
def stop_pipeline() -> str:
    return _stop_pipeline_impl()


@mcp.tool(
    name="get_new_messages",
    description="""
    获取流水线缓存的最新 OCR 结果（非阻塞）。

    参数：
    - max_count: 最大获取数量（默认 10），控制单次调用返回的消息数量上限

    返回值：
    - 成功：返回消息列表，每条消息包含以下字段：
      - type: 消息类型，固定为 "ocr"
      - ocr_results: OCR 识别结果列表，包含文字、坐标、置信度等信息
      - timestamp: OCR 时间戳
      - frame_id: 帧序号
    - 无新消息：返回空列表 []

    说明：
    此方法为非阻塞调用，立即返回当前队列中的 OCR 结果。
    获取后的消息会从队列中移除，不会重复返回。

    建议用法：
    1. 获取 ocr_results 后，直接使用文字结果进行分析决策
    2. 根据 OCR 结果中的坐标信息执行点击、滑动等操作
    3. 无需再调用 ocr() 工具，直接使用队列中的结果即可
    """,
)
def get_new_messages(max_count: int = 10) -> List[Dict[str, Any]]:
    return _get_new_messages_impl(max_count)


@mcp.tool(
    name="get_pipeline_status",
    description="""
    获取流水线的当前运行状态。

    参数：
    无

    返回值：
    返回状态字典，包含以下字段：
    - is_running: 是否正在运行（布尔值）
    - controller_id: 当前绑定的控制器 ID（字符串或 None）
    - uptime: 运行时长（秒，浮点数）
    - pending: 待处理消息数量（整数）

    说明：
    可用于检查流水线是否正常运行，以及监控消息队列的积压情况。
    """,
)
def get_pipeline_status() -> Dict[str, Any]:
    return _get_pipeline_status_impl()


# ==================== 主入口 ====================


def main():
    # 启动 MCP 服务器
    mcp.run()


if __name__ == "__main__":
    main()
