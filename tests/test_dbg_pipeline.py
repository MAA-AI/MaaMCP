"""集成测试：用 DbgController + 最小 bundle fixture 跑完整 pipeline 链路。

覆盖范围：
- run_pipeline 端到端：add_resource_path → Resource override_pipeline →
  Tasker post_task → TaskDetail 解析
- pipeline_override 端到端：post_task 级字段覆盖真实改变识别结果
- timeout_seconds 端到端：挂死的节点被工具侧超时主动停止
- DbgController fixture 能正常注册到 object_registry 并被 Tasker 找到

依赖：tests/conftest.py 的 maa_dbg_controller fixture
"""
import json
import time
from pathlib import Path

import pytest

from maa_mcp.pipeline_tools import run_pipeline

# 兼容 fastmcp 2.x (FunctionTool 包装) 与 3.x (保持原函数)
run_pipeline = getattr(run_pipeline, "fn", run_pipeline)  # type: ignore[arg-type]

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


@pytest.mark.integration
class TestDbgControllerPipelineOverrideTimeout:
    """pipeline_override + timeout_seconds 的端到端行为。

    DbgController 返回全黑帧（见 conftest），因此：
    - ColorMatch 找白色（lower/upper 250..255）永远不命中 → 用来构造挂死节点
    - ColorMatch 找黑色（lower/upper 0..5）必然命中 → 用来验证 override 生效
    """

    def _write_hang_pipeline(self, tmp_path: Path) -> Path:
        """一个在全黑帧上永远识别不命中的节点（节点级超时 60s）。"""
        p = tmp_path / "hang.json"
        p.write_text(
            json.dumps(
                {
                    "WaitWhite": {
                        "recognition": "ColorMatch",
                        "lower": [250, 250, 250],
                        "upper": [255, 255, 255],
                        "roi": [0, 0, 64, 64],
                        "timeout": 60000,
                        "action": "DoNothing",
                    }
                }
            ),
            encoding="utf-8",
        )
        return p

    def test_pipeline_override_flips_recognition_result(
        self, maa_dbg_controller, tmp_path
    ):
        """文件里的节点找白色（必失败）；override 改成找黑色 → 转为成功。

        证明 post_task 级 pipeline_override 做的是字段级合并，
        且不需要改 pipeline 文件就能改变单次运行的识别参数。
        """
        pipeline = self._write_hang_pipeline(tmp_path)
        result = run_pipeline(
            controller_id=maa_dbg_controller,
            pipeline_path=str(pipeline),
            entry="WaitWhite",
            resource_path=str(BUNDLE_FIXTURE),
            start_agent=False,
            pipeline_override={
                "WaitWhite": {"lower": [0, 0, 0], "upper": [5, 5, 5]}
            },
            timeout_seconds=30,
        )
        assert isinstance(result, dict), f"unexpected result: {result!r}"
        assert result["success"] is True, f"override 未生效: {result!r}"
        assert result["status"] == "succeeded"

    def test_timeout_stops_hanging_pipeline(self, maa_dbg_controller, tmp_path):
        """不命中的节点（节点级超时 60s）被 timeout_seconds=3 主动停止。

        没有工具侧超时的话，这个用例要挂满 60s 才返回。
        """
        pipeline = self._write_hang_pipeline(tmp_path)
        start = time.monotonic()
        result = run_pipeline(
            controller_id=maa_dbg_controller,
            pipeline_path=str(pipeline),
            entry="WaitWhite",
            resource_path=str(BUNDLE_FIXTURE),
            start_agent=False,
            timeout_seconds=3,
        )
        elapsed = time.monotonic() - start
        assert isinstance(result, dict), f"unexpected result: {result!r}"
        assert result["success"] is False
        assert result["status"] == "timeout"
        assert "timeout_seconds" in result["error"]
        # 远小于节点级超时 60s（给 post_stop 清理留些余量）
        assert elapsed < 30, f"超时停止耗时过长: {elapsed:.1f}s"

    def test_harmless_override_keeps_success(self, maa_dbg_controller):
        """对正常 pipeline 覆盖无关字段（post_delay）不影响成功结果。"""
        result = run_pipeline(
            controller_id=maa_dbg_controller,
            pipeline_path=str(BUNDLE_FIXTURE / "pipeline" / "entry.json"),
            entry="MyEntry",
            resource_path=str(BUNDLE_FIXTURE),
            start_agent=False,
            pipeline_override={"MyExit": {"post_delay": 0}},
            timeout_seconds=30,
        )
        assert isinstance(result, dict), f"unexpected result: {result!r}"
        assert result["success"] is True
        assert result["status"] == "succeeded"
