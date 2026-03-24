# maa_mcp/pipeline/state.py
"""
状态管理模块
============
流水线状态管理类。
"""

import threading
from threading import Lock, Event
from queue import Queue, Empty
from typing import Optional, Dict, Any


class PipelineState:
    """
    流水线全局状态（单例，线程安全）
    
    管理流水线的运行状态、消息队列和统计信息。
    """

    _instance = None
    _lock = Lock()  # 类属性：全局共享锁

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # 双重检查锁定
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.is_running = False
        self.stop_event = Event()
        self.pipeline_thread: Optional[threading.Thread] = None
        self.message_queue = Queue(maxsize=100)
        self.stats_dict: Dict[str, Any] = {}
        self.last_screen_state: Dict[str, Any] = {}
        self.controller_id: Optional[str] = None
        self.reset()

    def reset(self):
        """重置流水线状态"""
        with PipelineState._lock:
            self.is_running = False
            self.stop_event.clear()
            # 清空队列
            while not self.message_queue.empty():
                try:
                    self.message_queue.get_nowait()
                except Empty:
                    break
            self.stats_dict = {
                "frame_count": 0,
                "ocr_count": 0,
                "new_message_count": 0,
                "start_time": 0,
                "last_update": 0,
            }
            self.last_screen_state = {}
            self.controller_id = None

    def start(self, controller_id: str):
        """标记流水线启动"""
        with PipelineState._lock:
            self.is_running = True
            self.controller_id = controller_id

    def stop(self):
        """标记流水线停止"""
        with PipelineState._lock:
            self.is_running = False
            self.stop_event.set()

    def update_stats(self, **kwargs):
        """更新统计信息"""
        with PipelineState._lock:
            for key, value in kwargs.items():
                self.stats_dict[key] = value

    def increment_stat(self, key: str, amount: int = 1):
        """增加统计计数"""
        with PipelineState._lock:
            self.stats_dict[key] = self.stats_dict.get(key, 0) + amount

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息副本"""
        with PipelineState._lock:
            return dict(self.stats_dict)

    def update_screen_state(self, texts: list, timestamp: float):
        """更新屏幕状态"""
        with PipelineState._lock:
            self.last_screen_state["texts"] = texts
            self.last_screen_state["timestamp"] = timestamp

    def get_screen_state(self) -> Dict[str, Any]:
        """获取屏幕状态副本"""
        with PipelineState._lock:
            return dict(self.last_screen_state)


# 全局状态实例（懒加载）
_pipeline_state: Optional[PipelineState] = None


def get_pipeline_state() -> PipelineState:
    """
    获取流水线状态单例实例
    
    Returns:
        PipelineState 实例
    """
    global _pipeline_state
    if _pipeline_state is None:
        _pipeline_state = PipelineState()
    return _pipeline_state


__all__ = [
    "PipelineState",
    "get_pipeline_state",
]
