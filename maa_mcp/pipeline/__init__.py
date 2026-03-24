# maa_mcp/pipeline/__init__.py
"""
Pipeline 模块
=============
流水线服务器的核心组件。

包含：
- logging_config: 日志配置
- state: 流水线状态管理
"""

from .logging_config import setup_logger, get_logger
from .state import PipelineState, get_pipeline_state

__all__ = [
    # 日志
    "setup_logger",
    "get_logger",
    # 状态管理
    "PipelineState",
    "get_pipeline_state",
]
