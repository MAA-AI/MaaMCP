"""run_pipeline / benchmark_node 的 pipeline_override + timeout_seconds 测试。

背景：单节点验证时 run_pipeline 此前没有「只截 ROI 区域验证」的参数——
想收紧 roi 只能改 pipeline 文件；同时 task_job.wait() 无限阻塞，
识别未命中的节点会把 MCP 调用挂满整个节点超时（默认 20s）。

覆盖范围（纯函数 + monkeypatch 假件，不依赖 maafw 运行时）：
- _validate_timeout_seconds: 超时参数校验
- _validate_pipeline_override: override 结构校验
- _unknown_override_nodes: 未知节点名 warning 生成
- _wait_task_with_timeout: 轮询等待 / 超时 post_stop 路径
- run_pipeline: 新参数透传到 post_task、timeout 返回结构、默认值向后兼容
- benchmark_node: override 透传、next 保护、warnings 序列化

端到端（DbgController）见 tests/test_dbg_pipeline.py。
"""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from maa_mcp.pipeline_tools import (
    BenchmarkRunResult,
    _benchmark_node_impl,
    _unknown_override_nodes,
    _validate_pipeline_override,
    _validate_timeout_seconds,
    _wait_task_with_timeout,
    run_pipeline,
)

# 兼容 fastmcp 2.x (FunctionTool 包装) 与 3.x (保持原函数)
run_pipeline = getattr(run_pipeline, "fn", run_pipeline)  # type: ignore[arg-type]


# =============================================================================
# 纯函数单元测试
# =============================================================================


@pytest.mark.unit
class TestValidateTimeoutSeconds:
    """`_validate_timeout_seconds` 参数校验。"""

    def test_none_is_valid(self) -> None:
        """None（不限时）合法，不抛错。"""
        _validate_timeout_seconds(None)

    @pytest.mark.parametrize("value", [10, 0.5, 15.0, 1])
    def test_positive_numbers_valid(self, value) -> None:
        """正 int / float 合法。"""
        _validate_timeout_seconds(value)

    @pytest.mark.parametrize("value", [0, -1, -0.5])
    def test_non_positive_raises(self, value) -> None:
        """0 和负数非法。"""
        with pytest.raises(ValueError, match="必须 > 0"):
            _validate_timeout_seconds(value)

    @pytest.mark.parametrize("value", ["10", [10], True])
    def test_non_numeric_raises(self, value) -> None:
        """字符串 / list / bool 非法（bool 是 int 子类，需显式拒绝）。"""
        with pytest.raises(ValueError, match="必须是正数"):
            _validate_timeout_seconds(value)

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf"), 10**400]
    )
    def test_non_finite_raises(self, value) -> None:
        """NaN / ±inf / 超出 float 范围的巨大整数非法。

        JSON 层（jiter/pydantic）接受 NaN/Infinity/1e400；不拦的话
        deadline = monotonic() + NaN 会让超时分支永远不触发——
        静默禁用超时还带 CPU 空转轮询。
        """
        with pytest.raises(ValueError, match="有限数值"):
            _validate_timeout_seconds(value)


@pytest.mark.unit
class TestValidatePipelineOverride:
    """`_validate_pipeline_override` 结构校验。"""

    def test_none_returns_empty_dict(self) -> None:
        """None → 空 dict（等价于不覆盖）。"""
        assert _validate_pipeline_override(None) == {}

    def test_valid_override_passes_through(self) -> None:
        """合法结构原样返回。"""
        override = {"MyNode": {"roi": [0, 0, 100, 100], "timeout": 3000}}
        assert _validate_pipeline_override(override) is override

    def test_empty_dict_valid(self) -> None:
        """空 dict 合法（no-op 覆盖）。"""
        assert _validate_pipeline_override({}) == {}

    @pytest.mark.parametrize("value", ["not a dict", [{"A": {}}], 42])
    def test_top_level_non_dict_raises(self, value) -> None:
        """顶层非 dict 非法。"""
        with pytest.raises(ValueError, match="必须是 dict"):
            _validate_pipeline_override(value)

    def test_non_string_key_raises(self) -> None:
        """非字符串键非法。"""
        with pytest.raises(ValueError, match="非空字符串节点名"):
            _validate_pipeline_override({123: {"roi": [0, 0, 1, 1]}})

    def test_empty_string_key_raises(self) -> None:
        """空字符串键非法。"""
        with pytest.raises(ValueError, match="非空字符串节点名"):
            _validate_pipeline_override({"": {"roi": [0, 0, 1, 1]}})

    def test_node_value_non_dict_raises(self) -> None:
        """节点值非 dict 非法。"""
        with pytest.raises(ValueError, match="必须是字段 dict"):
            _validate_pipeline_override({"MyNode": [0, 0, 100, 100]})


@pytest.mark.unit
class TestUnknownOverrideNodes:
    """`_unknown_override_nodes` warning 生成。"""

    def test_all_known_no_warnings(self) -> None:
        """节点都在 merged 中 → 无 warning。"""
        merged = {"A": {}, "B": {}}
        assert _unknown_override_nodes({"A": {"roi": [0, 0, 1, 1]}}, merged) == []

    def test_unknown_node_warned_with_name(self) -> None:
        """未知节点 → warning 含节点名。"""
        warnings = _unknown_override_nodes({"Typo": {"roi": [0, 0, 1, 1]}}, {"A": {}})
        assert len(warnings) == 1
        assert "Typo" in warnings[0]

    def test_mixed_only_unknown_warned(self) -> None:
        """已知 + 未知混合 → 只对未知节点告警，且排序稳定。"""
        merged = {"A": {}}
        warnings = _unknown_override_nodes(
            {"A": {}, "Z_Unknown": {}, "B_Unknown": {}}, merged
        )
        assert len(warnings) == 2
        assert "B_Unknown" in warnings[0]
        assert "Z_Unknown" in warnings[1]

    def test_empty_override_no_warnings(self) -> None:
        """空 override → 无 warning。"""
        assert _unknown_override_nodes({}, {"A": {}}) == []


# =============================================================================
# _wait_task_with_timeout（SimpleNamespace 假件）
# =============================================================================


class FakeTaskJob:
    """按 done_after 次轮询后变 done 的假 TaskJob。

    done_after=0 表示立即 done；done_after=None 表示永远不 done。
    """

    def __init__(self, detail: Any, done_after: Optional[int] = 0):
        self._detail = detail
        self._done_after = done_after
        self._polls = 0
        self.wait_calls = 0

    @property
    def done(self) -> bool:
        if self._done_after is None:
            return False
        self._polls += 1
        return self._polls > self._done_after

    def wait(self) -> "FakeTaskJob":
        self.wait_calls += 1
        return self

    def get(self) -> Any:
        return self._detail


class FakeTasker:
    """记录 post_stop / post_task 调用的假 Tasker。"""

    def __init__(self, task_job: Optional[FakeTaskJob] = None):
        self._task_job = task_job
        self.post_stop_calls = 0
        self.post_task_calls: list = []

    def post_task(self, entry: str, pipeline_override: dict = {}) -> FakeTaskJob:
        self.post_task_calls.append((entry, pipeline_override))
        return self._task_job

    def post_stop(self):
        self.post_stop_calls += 1
        return SimpleNamespace(wait=lambda: None)


@pytest.mark.unit
class TestWaitTaskWithTimeout:
    """`_wait_task_with_timeout` 等待 / 超时行为。"""

    def _detail(self) -> SimpleNamespace:
        return SimpleNamespace(task_id=1, nodes=[])

    def test_none_timeout_blocks_via_wait(self) -> None:
        """timeout_seconds=None → 走 wait().get() 旧路径，不轮询不 post_stop。"""
        detail = self._detail()
        job = FakeTaskJob(detail, done_after=0)
        tasker = FakeTasker()
        result, timed_out = _wait_task_with_timeout(tasker, job, None)
        assert result is detail
        assert timed_out is False
        assert job.wait_calls == 1
        assert tasker.post_stop_calls == 0

    def test_done_immediately_returns_detail(self) -> None:
        """首次轮询已 done → 直接返回 detail，不 post_stop。"""
        detail = self._detail()
        job = FakeTaskJob(detail, done_after=0)
        tasker = FakeTasker()
        result, timed_out = _wait_task_with_timeout(
            tasker, job, timeout_seconds=5, poll_interval=0.01
        )
        assert result is detail
        assert timed_out is False
        assert tasker.post_stop_calls == 0

    def test_done_after_a_few_polls_returns_detail(self) -> None:
        """几次轮询后 done → 正常返回，不超时。"""
        detail = self._detail()
        job = FakeTaskJob(detail, done_after=3)
        tasker = FakeTasker()
        result, timed_out = _wait_task_with_timeout(
            tasker, job, timeout_seconds=5, poll_interval=0.01
        )
        assert result is detail
        assert timed_out is False
        assert tasker.post_stop_calls == 0

    def test_never_done_triggers_post_stop(self) -> None:
        """一直不 done → 超时后 post_stop 一次，返回 timed_out=True。"""
        detail = self._detail()
        job = FakeTaskJob(detail, done_after=None)
        tasker = FakeTasker()
        result, timed_out = _wait_task_with_timeout(
            tasker, job, timeout_seconds=0.05, poll_interval=0.01
        )
        assert timed_out is True
        assert tasker.post_stop_calls == 1
        # 停止后仍然回收 detail（携带部分节点信息）
        assert result is detail
        # 停止后通过 wait() 取 detail（stop 完成后 wait 立即返回）
        assert job.wait_calls == 1

    def test_timeout_shorter_than_poll_interval_still_checks_done_first(self) -> None:
        """timeout < poll_interval 时，done 检查仍先于超时判定。"""
        detail = self._detail()
        job = FakeTaskJob(detail, done_after=0)
        tasker = FakeTasker()
        result, timed_out = _wait_task_with_timeout(
            tasker, job, timeout_seconds=0.001, poll_interval=1.0
        )
        assert result is detail
        assert timed_out is False


# =============================================================================
# run_pipeline 新参数（monkeypatch 假 Resource / Tasker，不依赖 maafw 运行时）
# =============================================================================


def _write_pipeline(tmp_path: Path, name: str, nodes: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
    return p


def _success_detail() -> SimpleNamespace:
    status = SimpleNamespace(
        succeeded=True, failed=False, running=False, pending=False, done=True
    )
    return SimpleNamespace(status=status, task_id=7, nodes=[])


def _patch_resource_and_tasker(
    monkeypatch: pytest.MonkeyPatch, tasker: FakeTasker
) -> None:
    """把 pipeline_tools 里的 Resource / Tasker 换成假件。"""
    fake_resource = SimpleNamespace(override_pipeline=lambda merged: True)
    monkeypatch.setattr(
        "maa_mcp.pipeline_tools.get_or_create_resource", lambda: fake_resource
    )
    monkeypatch.setattr(
        "maa_mcp.pipeline_tools.get_or_create_tasker", lambda cid: tasker
    )


@pytest.mark.unit
class TestRunPipelineNewParamValidation:
    """新参数的预校验：非法输入返回错误字符串，且先于任何状态修改。"""

    def test_negative_timeout_returns_error(self, tmp_path: Path) -> None:
        f = _write_pipeline(tmp_path, "p.json", {"P": {"recognition": "DirectHit"}})
        result = run_pipeline(
            controller_id="fake", pipeline_path=str(f), timeout_seconds=-1
        )
        assert isinstance(result, str)
        assert "参数错误" in result
        assert "timeout_seconds" in result

    def test_non_dict_override_returns_error(self, tmp_path: Path) -> None:
        f = _write_pipeline(tmp_path, "p.json", {"P": {"recognition": "DirectHit"}})
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=str(f),
            pipeline_override="not a dict",
        )
        assert isinstance(result, str)
        assert "参数错误" in result
        assert "pipeline_override" in result

    def test_defaults_are_backward_compatible(self) -> None:
        """新参数默认值必须是 None（不改变旧行为）。"""
        sig = inspect.signature(run_pipeline)
        assert sig.parameters["pipeline_override"].default is None
        assert sig.parameters["timeout_seconds"].default is None


@pytest.mark.unit
class TestRunPipelineOverridePassthrough:
    """pipeline_override 透传到 tasker.post_task。"""

    def test_override_reaches_post_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """覆盖 dict 原样传给 post_task 第二参数。"""
        f = _write_pipeline(
            tmp_path, "p.json", {"MyNode": {"recognition": "DirectHit"}}
        )
        job = FakeTaskJob(_success_detail(), done_after=0)
        tasker = FakeTasker(job)
        _patch_resource_and_tasker(monkeypatch, tasker)

        override = {"MyNode": {"roi": [10, 20, 300, 100], "timeout": 3000}}
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=str(f),
            start_agent=False,
            pipeline_override=override,
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert result["success"] is True
        assert tasker.post_task_calls == [("MyNode", override)]
        # 已知节点：无 warnings
        assert "warnings" not in result

    def test_no_override_posts_empty_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不传 override → post_task 收到空 dict（与 maafw 默认一致）。"""
        f = _write_pipeline(
            tmp_path, "p.json", {"MyNode": {"recognition": "DirectHit"}}
        )
        job = FakeTaskJob(_success_detail(), done_after=0)
        tasker = FakeTasker(job)
        _patch_resource_and_tasker(monkeypatch, tasker)

        result = run_pipeline(
            controller_id="fake", pipeline_path=str(f), start_agent=False
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert tasker.post_task_calls == [("MyNode", {})]

    def test_unknown_override_node_warned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """覆盖了不在文件节点表中的节点名 → warnings 提示（不阻断执行）。"""
        f = _write_pipeline(
            tmp_path, "p.json", {"MyNode": {"recognition": "DirectHit"}}
        )
        job = FakeTaskJob(_success_detail(), done_after=0)
        tasker = FakeTasker(job)
        _patch_resource_and_tasker(monkeypatch, tasker)

        result = run_pipeline(
            controller_id="fake",
            pipeline_path=str(f),
            start_agent=False,
            pipeline_override={"TypoNode": {"roi": [0, 0, 1, 1]}},
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert result["success"] is True  # 未知名只警告，不失败
        assert any("TypoNode" in w for w in result.get("warnings", []))


@pytest.mark.unit
class TestRunPipelineTimeout:
    """timeout_seconds 超时路径的返回结构。"""

    def test_timeout_returns_structured_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务一直不结束 → status='timeout'，success=False，tasker 被 post_stop。"""
        f = _write_pipeline(
            tmp_path, "p.json", {"MyNode": {"recognition": "DirectHit"}}
        )
        # 超时后回收的部分 detail（status 无所谓，nodes 带一个已执行节点）
        partial_detail = SimpleNamespace(
            status=SimpleNamespace(
                succeeded=False, failed=True, running=False, pending=False, done=True
            ),
            task_id=9,
            nodes=[SimpleNamespace(name="MyNode", recognition=None)],
        )
        job = FakeTaskJob(partial_detail, done_after=None)
        tasker = FakeTasker(job)
        _patch_resource_and_tasker(monkeypatch, tasker)

        result = run_pipeline(
            controller_id="fake",
            pipeline_path=str(f),
            start_agent=False,
            timeout_seconds=0.05,
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert result["success"] is False
        assert result["status"] == "timeout"
        assert "timeout_seconds" in result["error"]
        assert tasker.post_stop_calls == 1
        # 超时前已执行的部分节点详情要带回来
        assert result["nodes"] == [{"name": "MyNode"}]
        assert result["entry"] == "MyNode"
        assert result["task_id"] == 9

    def test_fast_task_within_timeout_unaffected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务在限时内完成 → 与不限时结果一致。"""
        f = _write_pipeline(
            tmp_path, "p.json", {"MyNode": {"recognition": "DirectHit"}}
        )
        job = FakeTaskJob(_success_detail(), done_after=0)
        tasker = FakeTasker(job)
        _patch_resource_and_tasker(monkeypatch, tasker)

        result = run_pipeline(
            controller_id="fake",
            pipeline_path=str(f),
            start_agent=False,
            timeout_seconds=30,
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert tasker.post_stop_calls == 0


# =============================================================================
# benchmark_node 的 pipeline_override
# =============================================================================


@pytest.mark.unit
class TestBenchmarkNodePipelineOverride:
    """`_benchmark_node_impl` 的 override 透传与保护。"""

    def _pipeline(self, tmp_path: Path) -> Path:
        return _write_pipeline(
            tmp_path,
            "bench.json",
            {
                "Entry": {"recognition": "DirectHit", "next": ["Target"]},
                "Target": {"recognition": "OCR", "expected": "X"},
            },
        )

    def test_invalid_override_structure_returns_error(self, tmp_path: Path) -> None:
        result = _benchmark_node_impl(
            controller_id="fake",
            pipeline_path=str(self._pipeline(tmp_path)),
            node="Target",
            pipeline_override="oops",
        )
        assert isinstance(result, str)
        assert "参数错误" in result

    @pytest.mark.parametrize("protected", ["Entry", "Target"])
    def test_override_next_on_harness_nodes_rejected(
        self, tmp_path: Path, protected: str
    ) -> None:
        """覆盖 entry / node 的 next → 参数错误（保护隔离链路）。"""
        result = _benchmark_node_impl(
            controller_id="fake",
            pipeline_path=str(self._pipeline(tmp_path)),
            node="Target",
            pipeline_override={protected: {"next": ["Somewhere"]}},
        )
        assert isinstance(result, str)
        assert "参数错误" in result
        assert "next" in result

    def test_override_reaches_each_post_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """每次迭代的 post_task 都带上 override。"""
        detail = SimpleNamespace(
            status=SimpleNamespace(
                succeeded=True, failed=False, running=False, pending=False, done=True
            ),
            task_id=1,
            nodes=[],
        )

        class MultiJobTasker(FakeTasker):
            def post_task(self, entry, pipeline_override={}):
                self.post_task_calls.append((entry, pipeline_override))
                return FakeTaskJob(detail, done_after=0)

        tasker = MultiJobTasker()
        _patch_resource_and_tasker(monkeypatch, tasker)

        override = {"Target": {"roi": [0, 0, 50, 50], "timeout": 2000}}
        result = _benchmark_node_impl(
            controller_id="fake",
            pipeline_path=str(self._pipeline(tmp_path)),
            node="Target",
            iterations=3,
            pipeline_override=override,
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert len(tasker.post_task_calls) == 3
        assert all(call == ("Entry", override) for call in tasker.post_task_calls)

    def test_unknown_override_node_in_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未知节点名 → 返回值 warnings 提示。"""
        detail = SimpleNamespace(
            status=SimpleNamespace(
                succeeded=True, failed=False, running=False, pending=False, done=True
            ),
            task_id=1,
            nodes=[],
        )

        class MultiJobTasker(FakeTasker):
            def post_task(self, entry, pipeline_override={}):
                self.post_task_calls.append((entry, pipeline_override))
                return FakeTaskJob(detail, done_after=0)

        tasker = MultiJobTasker()
        _patch_resource_and_tasker(monkeypatch, tasker)

        result = _benchmark_node_impl(
            controller_id="fake",
            pipeline_path=str(self._pipeline(tmp_path)),
            node="Target",
            iterations=1,
            pipeline_override={"TypoNode": {"roi": [0, 0, 1, 1]}},
        )
        assert isinstance(result, dict), f"unexpected: {result!r}"
        assert any("TypoNode" in w for w in result.get("warnings", []))


@pytest.mark.unit
class TestBenchmarkRunResultWarnings:
    """BenchmarkRunResult.warnings 序列化行为。"""

    def _result(self, warnings) -> BenchmarkRunResult:
        return BenchmarkRunResult(
            node="Target",
            iterations=1,
            successes=1,
            min_score=0.9,
            max_score=0.9,
            mean_score=0.9,
            latency_ms=[100],
            all_results_samples=[],
            warnings=warnings,
        )

    def test_empty_warnings_omitted(self) -> None:
        """空 warnings 不序列化（保持返回体精简，与 PipelineLoadResult 一致）。"""
        assert "warnings" not in self._result([]).to_dict()

    def test_non_empty_warnings_serialized_as_new_list(self) -> None:
        """非空 warnings 序列化，且返回新 list。"""
        r = self._result(["w1"])
        d = r.to_dict()
        assert d["warnings"] == ["w1"]
        d["warnings"].append("w2")
        assert r.warnings == ["w1"]

    def test_default_warnings_empty(self) -> None:
        """不传 warnings 时默认空 list（向后兼容旧构造方式）。"""
        r = BenchmarkRunResult(
            node="T",
            iterations=1,
            successes=0,
            min_score=None,
            max_score=None,
            mean_score=None,
            latency_ms=[10],
            all_results_samples=[],
        )
        assert r.warnings == []
        assert "warnings" not in r.to_dict()
