"""pipeline_tools 模块单元测试（纯函数部分）。

只测纯函数，不依赖 maafw 运行时 / 真实 controller / Resource / Tasker。
benchmark_node 端到端跑通由 tests/test_dbg_pipeline.py 覆盖（@pytest.mark.integration）。

覆盖范围：
- _build_benchmark_override: override pipeline 构造、entry/target 校验
- _summarize_iteration: 单次迭代的 score / sample 抽取
- _aggregate_benchmark: 多次迭代聚合
- BenchmarkRunResult.to_dict: 字段完整
"""

from types import SimpleNamespace

import pytest

from maa_mcp.pipeline_tools import (
    BenchmarkRunResult,
    _BENCHMARK_DONE_NODE,
    _aggregate_benchmark,
    _build_benchmark_override,
    _summarize_iteration,
)


# ---------------------------------------------------------------------------
# _build_benchmark_override
# ---------------------------------------------------------------------------


class TestBuildBenchmarkOverride:
    """构造 entry → target → done 的 override pipeline。"""

    def _sample_base(self) -> dict:
        return {
            "Main": {
                "recognition": "DirectHit",
                "action": "DoNothing",
                "next": ["A", "B"],
            },
            "A": {
                "recognition": "OCR",
                "expected": "A",
                "action": "Click",
                "next": ["Done"],
                "post_delay": 100,
            },
            "B": {
                "recognition": "OCR",
                "expected": "B",
                "action": "Click",
                "next": ["Done"],
            },
            "Done": {
                "recognition": "DirectHit",
                "action": "DoNothing",
            },
        }

    def test_happy_path_three_nodes(self):
        base = self._sample_base()
        result = _build_benchmark_override("Main", "A", base)
        assert set(result.keys()) == {"Main", "A", _BENCHMARK_DONE_NODE}
        # Main.next 强制指向 A
        assert result["Main"]["next"] == ["A"]
        # A.next 强制指向 done（剥离原 next=[Done]）
        assert result["A"]["next"] == [_BENCHMARK_DONE_NODE]
        # A 的其他字段保留
        assert result["A"]["recognition"] == "OCR"
        assert result["A"]["expected"] == "A"
        assert result["A"]["post_delay"] == 100
        # done 是 DirectHit 占位
        assert result[_BENCHMARK_DONE_NODE] == {
            "recognition": "DirectHit",
            "action": "DoNothing",
        }

    def test_base_is_not_mutated(self):
        """_build_benchmark_override 必须返回新 dict，不能修改 base。"""
        base = self._sample_base()
        original_main_next = list(base["Main"]["next"])
        original_a_next = list(base["A"]["next"])
        _build_benchmark_override("Main", "A", base)
        assert base["Main"]["next"] == original_main_next
        assert base["A"]["next"] == original_a_next
        assert _BENCHMARK_DONE_NODE not in base

    def test_entry_missing_raises(self):
        base = self._sample_base()
        with pytest.raises(ValueError, match="entry 不在 base 中"):
            _build_benchmark_override("NotExist", "A", base)

    def test_target_missing_raises(self):
        base = self._sample_base()
        with pytest.raises(ValueError, match="target_node 不在 base 中"):
            _build_benchmark_override("Main", "NotExist", base)

    def test_entry_equals_target_raises(self):
        base = self._sample_base()
        with pytest.raises(ValueError, match="entry 与 target_node 必须不同"):
            _build_benchmark_override("A", "A", base)

    def test_done_node_name_conflict_raises(self):
        base = self._sample_base()
        with pytest.raises(ValueError, match="done_node 名.*与 base 中现有节点冲突"):
            # "Done" 是 base 中已有节点名，触发冲突
            _build_benchmark_override("Main", "A", base, done_node="Done")

    def test_custom_done_node_works(self):
        base = self._sample_base()
        result = _build_benchmark_override("Main", "A", base, done_node="MyDone")
        assert "MyDone" in result
        assert _BENCHMARK_DONE_NODE not in result


# ---------------------------------------------------------------------------
# _summarize_iteration
# ---------------------------------------------------------------------------


class TestSummarizeIteration:
    """从 NodeDetail 抽出 score 与 all_results 样本。"""

    def _make_recognition_detail(self, score_results):
        """score_results: List[(box, score)] 或 None 表示空"""
        all_results = []
        for box, score in (score_results or []):
            all_results.append(SimpleNamespace(box=box, score=score))
        return SimpleNamespace(
            name="Target",
            recognition=SimpleNamespace(all_results=all_results),
        )

    def test_with_results_returns_score_and_sample(self):
        detail = self._make_recognition_detail([((10, 20, 30, 40), 0.95)])
        score, sample = _summarize_iteration(detail, "Target")
        assert score == pytest.approx(0.95)
        assert sample == [{"box": [10, 20, 30, 40], "score": 0.95}]

    def test_multiple_results_sample_includes_all(self):
        detail = self._make_recognition_detail(
            [((1, 2, 3, 4), 0.9), ((5, 6, 7, 8), 0.8)]
        )
        score, sample = _summarize_iteration(detail, "Target")
        # score 取 all_results[0]
        assert score == pytest.approx(0.9)
        assert len(sample) == 2

    def test_no_results_returns_none_score_and_empty_sample(self):
        detail = self._make_recognition_detail(None)
        score, sample = _summarize_iteration(detail, "Target")
        assert score is None
        assert sample == []

    def test_name_mismatch_returns_none(self):
        detail = self._make_recognition_detail([((1, 2, 3, 4), 0.9)])
        score, sample = _summarize_iteration(detail, "OtherNode")
        assert score is None
        assert sample == []

    def test_none_node_detail_returns_none(self):
        score, sample = _summarize_iteration(None, "Target")
        assert score is None
        assert sample == []

    def test_score_non_numeric_returns_none(self):
        all_results = [SimpleNamespace(box=(1, 2, 3, 4), score="not_a_number")]
        detail = SimpleNamespace(
            name="Target",
            recognition=SimpleNamespace(all_results=all_results),
        )
        score, sample = _summarize_iteration(detail, "Target")
        assert score is None
        # 样本里 score 仍写出 None（不丢信息）
        assert sample == [{"box": [1, 2, 3, 4], "score": None}]

    def test_box_none_serializes_as_none(self):
        all_results = [SimpleNamespace(box=None, score=0.5)]
        detail = SimpleNamespace(
            name="Target",
            recognition=SimpleNamespace(all_results=all_results),
        )
        score, sample = _summarize_iteration(detail, "Target")
        assert score == pytest.approx(0.5)
        assert sample == [{"box": None, "score": 0.5}]


# ---------------------------------------------------------------------------
# _aggregate_benchmark
# ---------------------------------------------------------------------------


class TestAggregateBenchmark:
    """聚合 (score, sample, latency_ms) 列表成 BenchmarkRunResult。"""

    def test_all_hits(self):
        per = [
            (0.90, [{"box": [1, 2, 3, 4], "score": 0.90}], 100),
            (0.95, [{"box": [1, 2, 3, 4], "score": 0.95}], 110),
            (0.80, [{"box": [1, 2, 3, 4], "score": 0.80}], 90),
        ]
        result = _aggregate_benchmark("Target", per)
        assert result.node == "Target"
        assert result.iterations == 3
        assert result.successes == 3
        assert result.min_score == pytest.approx(0.80)
        assert result.max_score == pytest.approx(0.95)
        assert result.mean_score == pytest.approx(0.88333, rel=1e-3)
        assert result.latency_ms == [100, 110, 90]

    def test_all_misses(self):
        per = [(None, [], 50), (None, [], 60)]
        result = _aggregate_benchmark("Target", per)
        assert result.successes == 0
        assert result.min_score is None
        assert result.max_score is None
        assert result.mean_score is None
        # 样本仍保留（即使是空 list）
        assert result.all_results_samples == [[], []]

    def test_mixed_hits_and_misses(self):
        per = [
            (0.9, [{"box": [1, 2, 3, 4], "score": 0.9}], 100),
            (None, [], 80),
            (0.7, [{"box": [1, 2, 3, 4], "score": 0.7}], 120),
        ]
        result = _aggregate_benchmark("Target", per)
        assert result.iterations == 3
        assert result.successes == 2
        assert result.min_score == pytest.approx(0.7)
        assert result.max_score == pytest.approx(0.9)
        assert result.mean_score == pytest.approx(0.8)

    def test_samples_capped_to_first_three(self):
        """_aggregate_benchmark 主动把 sample 截到前 3 个，避免返回体过大。"""
        per = [(0.5, [{"box": [i] * 4, "score": 0.5}], 10) for i in range(10)]
        result = _aggregate_benchmark("Target", per)
        assert result.iterations == 10
        assert result.successes == 10
        assert len(result.all_results_samples) == 3
        # 前 3 个 sample 的 box 第 0 个元素分别是 0/1/2
        assert result.all_results_samples[0][0]["box"][0] == 0
        assert result.all_results_samples[1][0]["box"][0] == 1
        assert result.all_results_samples[2][0]["box"][0] == 2


# ---------------------------------------------------------------------------
# BenchmarkRunResult.to_dict
# ---------------------------------------------------------------------------


class TestBenchmarkRunResultToDict:
    def test_to_dict_includes_all_fields(self):
        result = BenchmarkRunResult(
            node="Target",
            iterations=2,
            successes=2,
            min_score=0.9,
            max_score=0.95,
            mean_score=0.925,
            latency_ms=[100, 110],
            all_results_samples=[[{"box": [1, 2, 3, 4], "score": 0.95}]],
        )
        d = result.to_dict()
        assert d == {
            "node": "Target",
            "iterations": 2,
            "successes": 2,
            "min_score": 0.9,
            "max_score": 0.95,
            "mean_score": 0.925,
            "latency_ms": [100, 110],
            "all_results_samples": [
                [{"box": [1, 2, 3, 4], "score": 0.95}]
            ],
        }

    def test_to_dict_returns_new_lists(self):
        """to_dict 返回的 latency_ms / samples 是新 list，外部修改不影响 result。"""
        result = BenchmarkRunResult(
            node="Target",
            iterations=1,
            successes=1,
            min_score=0.5,
            max_score=0.5,
            mean_score=0.5,
            latency_ms=[100],
            all_results_samples=[],
        )
        d = result.to_dict()
        d["latency_ms"].append(999)
        assert result.latency_ms == [100]  # 没被外部修改
