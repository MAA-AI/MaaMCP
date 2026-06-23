"""集成测试：用 DbgController + 最小 bundle fixture 跑完整 pipeline 链路。

覆盖范围：
- run_pipeline 端到端：add_resource_path → Resource override_pipeline →
  Tasker post_task → TaskDetail 解析
- DbgController fixture 能正常注册到 object_registry 并被 Tasker 找到

依赖：tests/conftest.py 的 maa_dbg_controller fixture
"""
from pathlib import Path

import pytest

from maa_mcp.pipeline_tools import run_pipeline

BUNDLE_FIXTURE = Path(__file__).parent / "fixtures" / "bundle_minimal"


@pytest.mark.integration
class TestDbgControllerPipeline:
    """run_pipeline 跑通最小 DirectHit pipeline。"""

    def test_directhit_pipeline_succeeds(self, maa_dbg_controller):
        """两个 DirectHit 节点串联，期望 success=True。"""
        result = run_pipeline(
            controller_id=maa_dbg_controller,
            pipeline_path=str(BUNDLE_FIXTURE / "pipeline" / "entry.json"),
            entry="MyEntry",
            resource_path=str(BUNDLE_FIXTURE),
            start_agent=False,
        )
        # 成功路径返回 dict；失败路径返回 str 错误信息
        assert isinstance(result, dict), f"unexpected result: {result!r}"
        assert result["success"] is True
        assert result["entry"] == "MyEntry"
        assert result["node_count"] >= 2  # MyEntry + MyExit
        assert result["status"] == "succeeded"
