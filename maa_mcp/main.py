from maa_mcp.core import mcp

# 导入各模块以注册工具
from maa_mcp import adb  # noqa: F401
from maa_mcp import win32  # noqa: F401
from maa_mcp import download  # noqa: F401
from maa_mcp import resource  # noqa: F401
from maa_mcp import vision  # noqa: F401
from maa_mcp import control  # noqa: F401
from maa_mcp import utils  # noqa: F401
from maa_mcp import pipeline_tools  # noqa: F401
from maa_mcp import agent_supervisor  # noqa: F401  # 触发 atexit.shutdown_all 注册
