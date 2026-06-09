"""多 Pipeline JSON 文件加载相关测试。

覆盖：
- 纯函数单元测试：路径规范化、文件读取与校验、节点合并、入口校验
- 集成测试：load_pipeline / run_pipeline 多文件场景、跨文件 next 引用
"""

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from maa_mcp.pipeline_tools import (
    ConflictStrategy,
    PipelineLoadResult,
    _merge_pipelines,
    _normalize_paths,
    _read_and_validate_pipelines,
    _validate_entry,
    load_pipeline,
    run_pipeline,
)

# 兼容 fastmcp 2.x (FunctionTool 包装) 与 3.x (保持原函数)：
# 2.x 下 @mcp.tool 装饰后是 FunctionTool 对象，调用需 .fn；
# 3.x 下装饰后仍是函数，可直接调用。
load_pipeline = getattr(load_pipeline, "fn", load_pipeline)  # type: ignore[arg-type]
run_pipeline = getattr(run_pipeline, "fn", run_pipeline)  # type: ignore[arg-type]


# =============================================================================
# 纯函数单元测试
# =============================================================================


@pytest.mark.unit
class TestNormalizePaths:
    """`_normalize_paths` 纯函数测试。"""

    def test_str_wrapped_to_single_element_list(self) -> None:
        """str 输入应被包装为单元素 list。"""
        assert _normalize_paths("main.json") == ["main.json"]

    def test_list_preserves_order(self) -> None:
        """list 输入应保持原顺序。"""
        assert _normalize_paths(["a.json", "b.json", "c.json"]) == [
            "a.json",
            "b.json",
            "c.json",
        ]

    def test_single_element_list_passes_through(self) -> None:
        """1-元素 list 与 str 行为等价。"""
        assert _normalize_paths(["main.json"]) == ["main.json"]

    def test_empty_string_filtered_out(self) -> None:
        """空字符串应被过滤。"""
        assert _normalize_paths("") == []
        assert _normalize_paths(["a.json", "", "b.json"]) == ["a.json", "b.json"]

    def test_none_in_list_filtered_out(self) -> None:
        """list 中的 None 应被过滤。"""
        assert _normalize_paths(["a.json", None, "b.json"]) == ["a.json", "b.json"]

    def test_all_empty_returns_empty_list(self) -> None:
        """全空输入应返回空 list。"""
        assert _normalize_paths([]) == []
        assert _normalize_paths(["", None]) == []

    def test_non_string_non_list_rejected(self) -> None:
        """非 str / 非 list 输入应抛错。"""
        with pytest.raises(TypeError):
            _normalize_paths(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            _normalize_paths(None)  # type: ignore[arg-type]


@pytest.mark.unit
class TestReadAndValidatePipelines:
    """`_read_and_validate_pipelines` 纯函数测试。"""

    def test_happy_path(self, tmp_path: Path) -> None:
        """合法 JSON 文件应正确读取。"""
        p = tmp_path / "a.json"
        p.write_text(
            json.dumps({"NodeA": {"recognition": "DirectHit"}}), encoding="utf-8"
        )
        out = _read_and_validate_pipelines([str(p)])
        assert len(out) == 1
        assert out[0][0] == str(p.absolute())
        assert out[0][1] == {"NodeA": {"recognition": "DirectHit"}}

    def test_multiple_files_all_read(self, tmp_path: Path) -> None:
        """多文件全部读取。"""
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps({"A": {"x": 1}}), encoding="utf-8")
        f2.write_text(json.dumps({"B": {"x": 2}}), encoding="utf-8")
        out = _read_and_validate_pipelines([str(f1), str(f2)])
        assert len(out) == 2
        assert out[0][1] == {"A": {"x": 1}}
        assert out[1][1] == {"B": {"x": 2}}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """不存在的文件应抛 ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            _read_and_validate_pipelines([str(tmp_path / "nope.json")])

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        """目录路径应抛 ValueError。"""
        with pytest.raises(ValueError, match="不是文件"):
            _read_and_validate_pipelines([str(tmp_path)])

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        """JSON 解析失败应抛 ValueError。"""
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON 解析失败"):
            _read_and_validate_pipelines([str(p)])

    def test_empty_dict_raises(self, tmp_path: Path) -> None:
        """空 dict 应抛 ValueError。"""
        p = tmp_path / "empty.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="非空对象"):
            _read_and_validate_pipelines([str(p)])

    def test_top_level_array_raises(self, tmp_path: Path) -> None:
        """顶层为数组应抛 ValueError。"""
        p = tmp_path / "arr.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="非空对象"):
            _read_and_validate_pipelines([str(p)])

    def test_first_invalid_short_circuits(self, tmp_path: Path) -> None:
        """第一个文件无效时应立即抛错（不会读取后续文件）。"""
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        p1.write_text("not json", encoding="utf-8")
        p2.write_text(json.dumps({"B": {}}), encoding="utf-8")
        with pytest.raises(ValueError, match="a.json"):
            _read_and_validate_pipelines([str(p1), str(p2)])


@pytest.mark.unit
class TestMergePipelines:
    """`_merge_pipelines` 纯函数测试。

    合并语义：
    - STRICT 模式（默认）：检测到任何节点冲突立即抛 ValueError
    - OVERWRITE 模式：后加载的整节点覆盖先加载的（与 MaaFramework 行为一致）
    """

    def test_no_conflict_merges_all(self) -> None:
        """无冲突场景：所有节点合并。"""
        d1 = {"A": {"x": 1}}
        d2 = {"B": {"x": 2}}
        merged, conflicts = _merge_pipelines(
            [("f1", d1), ("f2", d2)], ConflictStrategy.STRICT
        )
        assert merged == {"A": {"x": 1}, "B": {"x": 2}}
        assert conflicts == []

    def test_strict_mode_raises_on_conflict(self) -> None:
        """STRICT 模式 + 冲突 → 抛 ValueError，含节点名 + 来源文件。"""
        d1 = {"Shared": {"v": "from_f1"}}
        d2 = {"Shared": {"v": "from_f2"}}
        with pytest.raises(ValueError, match="节点冲突.*Shared.*f2"):
            _merge_pipelines(
                [("f1", d1), ("f2", d2)], ConflictStrategy.STRICT
            )

    def test_overwrite_mode_replaces_node(self) -> None:
        """OVERWRITE 模式：后节点整节点替换前节点。"""
        d1 = {"Shared": {"v": "from_f1"}}
        d2 = {"Shared": {"v": "from_f2"}, "B": {"y": 1}}
        merged, conflicts = _merge_pipelines(
            [("f1", d1), ("f2", d2)], ConflictStrategy.OVERWRITE
        )
        assert merged["Shared"] == {"v": "from_f2"}
        assert merged["B"] == {"y": 1}
        assert "Shared" in conflicts

    def test_overwrite_does_not_deep_merge_node_fields(self) -> None:
        """OVERWRITE 模式是整节点替换，不做字段级深合并。"""
        d1 = {"A": {"recognition": "OCR", "expected": "old", "roi": [0, 0, 100, 100]}}
        d2 = {"A": {"recognition": "TemplateMatch", "expected": "new"}}
        merged, _ = _merge_pipelines(
            [("f1", d1), ("f2", d2)], ConflictStrategy.OVERWRITE
        )
        # d2 整体覆盖 d1：roi 字段丢失
        assert merged["A"] == {
            "recognition": "TemplateMatch",
            "expected": "new",
        }
        assert "roi" not in merged["A"]

    def test_conflict_list_deduped(self) -> None:
        """OVERWRITE 模式下 conflicts 列表去重。"""
        d1 = {"A": {"v": 1}}
        d2 = {"A": {"v": 2}}
        d3 = {"A": {"v": 3}}
        _, conflicts = _merge_pipelines(
            [("f1", d1), ("f2", d2), ("f3", d3)], ConflictStrategy.OVERWRITE
        )
        assert conflicts == ["A"]


@pytest.mark.unit
class TestValidateEntry:
    """`_validate_entry` 纯函数测试。"""

    def test_explicit_entry_present_returns_unchanged(self) -> None:
        """显式指定且在 merged 中 → 原样返回。"""
        merged = {"A": {}, "B": {}}
        assert _validate_entry("A", merged, {"A": {}, "B": {}}) == "A"

    def test_none_entry_uses_first_key_of_first_file(self) -> None:
        """None → 使用首文件的第一个 key。"""
        merged = {"A": {}, "B": {}}
        assert _validate_entry(None, merged, {"A": {}, "B": {}}) == "A"

    def test_explicit_entry_missing_raises(self) -> None:
        """显式指定但不在 merged → ValueError。"""
        merged = {"A": {}}
        with pytest.raises(ValueError, match=".*不存在.*可用节点.*"):
            _validate_entry("Missing", merged, {"A": {}})

    def test_first_file_dict_used_for_default_entry(self) -> None:
        """首文件的第一个 key 作为默认入口，即使后续文件有不同的首 key。"""
        # first_file_dict 第一个 key 是 "Start"
        # merged 中包含 "Start" 和 "Battle"
        first = {"Start": {}, "Other": {}}
        merged = {"Start": {}, "Battle": {}, "Other": {}}
        assert _validate_entry(None, merged, first) == "Start"


@pytest.mark.unit
class TestConflictStrategyEnum:
    """`ConflictStrategy` 枚举测试。"""

    def test_strict_is_default(self) -> None:
        """STRICT 必须是默认策略。"""
        assert ConflictStrategy.STRICT.value == "strict"
        # 默认值在 run_pipeline 签名中应为 "strict"
        import inspect

        sig = inspect.signature(run_pipeline)
        assert sig.parameters["on_conflict"].default == "strict"

    def test_overwrite_value(self) -> None:
        """OVERWRITE 值正确。"""
        assert ConflictStrategy.OVERWRITE.value == "overwrite"


@pytest.mark.unit
class TestPipelineLoadResult:
    """`PipelineLoadResult` dataclass 序列化测试。"""

    def test_minimal_success(self) -> None:
        """最小成功结果。"""
        r = PipelineLoadResult(success=True)
        d = r.to_dict()
        assert d["success"] is True
        assert "files" not in d
        assert "warnings" not in d
        assert "error" not in d

    def test_full_fields_serialized(self) -> None:
        """全字段结果正确序列化。"""
        r = PipelineLoadResult(
            success=False,
            files=["a.json", "b.json"],
            node_count=10,
            entry="Main",
            status="failed",
            task_id=42,
            nodes=[{"name": "X"}],
            warnings=["conflict on Y"],
            error="something went wrong",
        )
        d = r.to_dict()
        assert d["success"] is False
        assert d["files"] == ["a.json", "b.json"]
        assert d["node_count"] == 10
        assert d["entry"] == "Main"
        assert d["status"] == "failed"
        assert d["task_id"] == 42
        assert d["nodes"] == [{"name": "X"}]
        assert d["warnings"] == ["conflict on Y"]
        assert d["error"] == "something went wrong"

    def test_frozen(self) -> None:
        """dataclass 应为 frozen。"""
        r = PipelineLoadResult(success=True)
        with pytest.raises(Exception):  # FrozenInstanceError
            r.success = False  # type: ignore[misc]


# =============================================================================
# 集成测试（依赖 maafw）
# =============================================================================


@pytest.mark.integration
class TestLoadPipelineMulti:
    """`load_pipeline` 多文件集成测试（不写入 Resource）。"""

    def _write(self, tmp_path: Path, name: str, data: dict[str, Any]) -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_single_file_returns_dict_content(self, tmp_path: Path) -> None:
        """单文件：返回内容本身（向后兼容）。"""
        f = self._write(tmp_path, "a.json", {"A": {"recognition": "DirectHit"}})
        result = load_pipeline(str(f))
        assert result == {"A": {"recognition": "DirectHit"}}

    def test_single_element_list_returns_dict_content(self, tmp_path: Path) -> None:
        """1-元素 list：与单文件行为一致。"""
        f = self._write(tmp_path, "a.json", {"A": {"recognition": "DirectHit"}})
        result = load_pipeline([str(f)])
        assert result == {"A": {"recognition": "DirectHit"}}

    def test_multi_files_returns_path_to_content_mapping(
        self, tmp_path: Path
    ) -> None:
        """多文件：返回 {abs_path: content} 映射。"""
        f1 = self._write(tmp_path, "a.json", {"A": {"x": 1}})
        f2 = self._write(tmp_path, "b.json", {"B": {"x": 2}})
        result = load_pipeline([str(f1), str(f2)])
        assert isinstance(result, dict)
        assert str(f1.absolute()) in result
        assert str(f2.absolute()) in result
        assert result[str(f1.absolute())] == {"A": {"x": 1}}
        assert result[str(f2.absolute())] == {"B": {"x": 2}}

    def test_missing_file_returns_error_string(self, tmp_path: Path) -> None:
        """文件不存在：返回错误字符串。"""
        result = load_pipeline(str(tmp_path / "nope.json"))
        assert isinstance(result, str)
        assert "不存在" in result

    def test_invalid_json_returns_error_string(self, tmp_path: Path) -> None:
        """非法 JSON：返回错误字符串。"""
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        result = load_pipeline(str(f))
        assert isinstance(result, str)
        assert "JSON 解析失败" in result


@pytest.mark.integration
class TestRunPipelineMultiPreValidation:
    """`run_pipeline` 预校验阶段测试（在调用 override_pipeline 之前）。"""

    def test_empty_path_returns_error(self) -> None:
        """空路径 → 错误字符串，不触碰 Resource。"""
        result = run_pipeline(controller_id="fake", pipeline_path=[])
        assert isinstance(result, str)
        assert "至少包含一个" in result

    def test_empty_string_returns_error(self) -> None:
        """空字符串 → 错误字符串。"""
        result = run_pipeline(controller_id="fake", pipeline_path="")
        assert isinstance(result, str)
        assert "至少包含一个" in result

    def test_nonexistent_file_returns_error(self, tmp_path: Path) -> None:
        """不存在的文件 → 错误字符串，Resource 未污染。"""
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(tmp_path / "nope.json")],
        )
        assert isinstance(result, str)
        assert "不存在" in result

    def test_first_file_invalid_second_file_untouched(
        self, tmp_path: Path
    ) -> None:
        """第一文件无效 → 立即返回错误，不读第二文件。"""
        f1 = tmp_path / "bad.json"
        f2 = tmp_path / "good.json"
        f1.write_text("not json", encoding="utf-8")
        f2.write_text(json.dumps({"B": {}}), encoding="utf-8")
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(f1), str(f2)],
        )
        assert isinstance(result, str)
        assert "bad.json" in result

    def test_conflict_in_strict_mode_returns_error(
        self, tmp_path: Path
    ) -> None:
        """STRICT 模式 + 节点冲突 → 错误字符串，Resource 未污染。"""
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps({"Shared": {"v": 1}}), encoding="utf-8")
        f2.write_text(json.dumps({"Shared": {"v": 2}}), encoding="utf-8")
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(f1), str(f2)],
            on_conflict="strict",
        )
        assert isinstance(result, str)
        assert "节点冲突" in result
        assert "Shared" in result

    def test_overwrite_mode_silently_merges(self, tmp_path: Path) -> None:
        """OVERWRITE 模式 + 节点冲突 → 不报错（实际执行会失败，因为没真实 controller）。"""
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps({"Shared": {"v": 1}}), encoding="utf-8")
        f2.write_text(json.dumps({"Shared": {"v": 2}}), encoding="utf-8")
        # 用 fake controller_id 触发 tasker 创建失败 → 但已通过预校验
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(f1), str(f2)],
            on_conflict="overwrite",
        )
        # 不应返回 "节点冲突" 错误
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "节点冲突" not in result


@pytest.mark.integration
class TestRunPipelineCallSignatures:
    """验证 `run_pipeline` 三种调用方式都接受并通过预校验。

    这组测试不验证实际执行成功（需要真实 controller），
    只验证"预校验通过 → 进入 tasker/Resource 创建阶段"。
    """

    def _write_pipeline(
        self, tmp_path: Path, name: str, nodes: dict[str, Any]
    ) -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(nodes), encoding="utf-8")
        return p

    def test_str_single_file_accepted(
        self, tmp_path: Path
    ) -> None:
        """str 单文件：通过预校验。"""
        f = self._write_pipeline(
            tmp_path, "main.json", {"MainStart": {"recognition": "DirectHit"}}
        )
        result = run_pipeline(controller_id="fake", pipeline_path=str(f))
        # 预校验通过；tasker 创建会失败（fake controller）→ 返回字符串错误
        # 但错误不应是 "至少包含一个" / "不存在" / "JSON 解析失败"
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "至少包含一个" not in result
            assert "不存在" not in result
            assert "JSON 解析失败" not in result

    def test_single_element_list_accepted(
        self, tmp_path: Path
    ) -> None:
        """1-元素 list：通过预校验。"""
        f = self._write_pipeline(
            tmp_path, "main.json", {"MainStart": {"recognition": "DirectHit"}}
        )
        result = run_pipeline(controller_id="fake", pipeline_path=[str(f)])
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "至少包含一个" not in result
            assert "不存在" not in result

    def test_multi_file_accepted(self, tmp_path: Path) -> None:
        """多文件 list：通过预校验。"""
        f1 = self._write_pipeline(
            tmp_path, "main.json", {"MainStart": {"recognition": "DirectHit"}}
        )
        f2 = self._write_pipeline(
            tmp_path, "battle.json", {"Battle": {"recognition": "DirectHit"}}
        )
        f3 = self._write_pipeline(
            tmp_path, "login.json", {"Login": {"recognition": "DirectHit"}}
        )
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(f1), str(f2), str(f3)],
        )
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "至少包含一个" not in result
            assert "不存在" not in result

    def test_cross_file_node_references_resolve(
        self, tmp_path: Path
    ) -> None:
        """跨文件 next 引用：在 strict 模式下应通过预校验。

        关键：login.json 的 Login 节点 next 引用 main.json 的 MainStart 节点，
        合并后两节点在同一个 namespace，必须无冲突才能写入 Resource。
        """
        f1 = self._write_pipeline(
            tmp_path,
            "main.json",
            {"MainStart": {"recognition": "DirectHit", "next": ["Login"]}},
        )
        f2 = self._write_pipeline(
            tmp_path,
            "login.json",
            {"Login": {"recognition": "DirectHit", "next": ["MainStart"]}},
        )
        result = run_pipeline(
            controller_id="fake",
            pipeline_path=[str(f1), str(f2)],
            on_conflict="strict",
        )
        # 预校验通过（无冲突）；后续 tasker 失败 → 字符串错误，但非冲突错误
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "节点冲突" not in result
            assert "不存在" not in result
            assert "JSON 解析失败" not in result

    def test_run_pipeline_does_not_raise_name_error(
        self, tmp_path: Path
    ) -> None:
        """回归测试：run_pipeline 内部所有 logger.xxx 调用必须能找到 logger 名字。

        防止类似"在 run_pipeline 里调 logger.info 但模块顶部没 import logger"的 bug 复发。
        """
        f = self._write_pipeline(
            tmp_path,
            "p.json",
            {"P": {"recognition": "DirectHit"}},
        )
        # controller_id 故意传非法值，让流程走到 logger 那一步
        result = run_pipeline(
            controller_id="ctrl_name_error_test",
            pipeline_path=str(f),
        )
        # 不应抛 NameError；预期是字符串错误（tasker 不存在）
        assert isinstance(result, str) or isinstance(result, dict)
        if isinstance(result, str):
            assert "name 'logger' is not defined" not in result
            assert "NameError" not in result
