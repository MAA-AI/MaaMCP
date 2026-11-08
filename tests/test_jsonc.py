"""JSONC（带注释的 JSON）支持测试 (issue #38)。

覆盖：
- `maa_mcp.jsonc.loads` 单元行为：注释、尾逗号、严格性边界、错误定位
- pipeline 文件读取：`load_pipeline` / `_read_and_validate_pipelines`
- `save_pipeline`：接受带注释的输入，落盘仍是标准 JSON
- `agent_supervisor`：读取带注释的 interface.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_mcp.agent_supervisor import load_agent_config
from maa_mcp.jsonc import loads
from maa_mcp.pipeline_tools import (
    _read_and_validate_pipelines,
    load_pipeline,
    save_pipeline,
)

# fastmcp 2.x wraps @mcp.tool functions in FunctionTool; 3.x does not.
load_pipeline = getattr(load_pipeline, "fn", load_pipeline)
save_pipeline = getattr(save_pipeline, "fn", save_pipeline)


# =============================================================================
# jsonc.loads 单元测试
# =============================================================================


@pytest.mark.unit
class TestJsoncLoads:
    """`jsonc.loads` 单元测试。"""

    def test_plain_json_unchanged(self) -> None:
        """纯 JSON 应原样解析（JSON 是 JSONC 的子集）。"""
        assert loads('{"a": 1, "b": [1, 2]}') == {"a": 1, "b": [1, 2]}

    def test_line_comment_ignored(self) -> None:
        """`//` 行注释应被忽略。"""
        text = """
        {
          // 这是节点 A
          "A": {"recognition": "DirectHit"}
        }
        """
        assert loads(text) == {"A": {"recognition": "DirectHit"}}

    def test_block_comment_ignored(self) -> None:
        """`/* */` 块注释应被忽略。"""
        text = '{ /* 头部说明 */ "A": 1 }'
        assert loads(text) == {"A": 1}

    def test_multiline_block_comment_ignored(self) -> None:
        """跨行块注释应被忽略。"""
        text = '{\n/* 第一行\n   第二行 */\n"A": 1\n}'
        assert loads(text) == {"A": 1}

    def test_trailing_comma_in_object(self) -> None:
        """对象尾逗号应被接受（MaaFramework 同样接受）。"""
        assert loads('{"A": 1, "B": 2,}') == {"A": 1, "B": 2}

    def test_trailing_comma_in_array(self) -> None:
        """数组尾逗号应被接受。"""
        assert loads('{"a": [1, 2, 3,]}') == {"a": [1, 2, 3]}

    def test_comment_markers_inside_string_preserved(self) -> None:
        """字符串里的 `//` 与 `/* */` 是普通字符，不能被当注释剥掉。"""
        text = json.dumps(
            {"url": "https://example.com/a//b", "glob": "/* not a comment */"}
        )
        assert loads(text) == {
            "url": "https://example.com/a//b",
            "glob": "/* not a comment */",
        }

    def test_escaped_quote_does_not_break_comment_detection(self) -> None:
        """字符串里的转义引号不应让后续内容被误判为字符串。"""
        text = '{"a": "he said \\"hi\\" // still in string", "b": 2}'
        assert loads(text) == {"a": 'he said "hi" // still in string', "b": 2}

    def test_comment_between_tokens(self) -> None:
        """注释可以出现在任意 token 之间。"""
        text = '{/*a*/"A"/*b*/:/*c*/1/*d*/,/*e*/"B"/*f*/:/*g*/2/*h*/}'
        assert loads(text) == {"A": 1, "B": 2}

    def test_unterminated_block_comment_raises(self) -> None:
        """未闭合的块注释应报错并给出位置。"""
        with pytest.raises(json.JSONDecodeError, match="Unterminated comment"):
            loads('{"A": 1} /* 没闭合')

    def test_duplicate_key_rejected(self) -> None:
        """重复键应报错（meojson 同样拒绝）。"""
        with pytest.raises(json.JSONDecodeError, match="Duplicate key"):
            loads('{"A": 1, "A": 2}')

    def test_nan_rejected(self) -> None:
        """NaN 不是合法 JSON，必须拒绝（stdlib json 默认接受）。"""
        with pytest.raises(json.JSONDecodeError, match="not valid JSON"):
            loads('{"A": NaN}')

    def test_infinity_rejected(self) -> None:
        """Infinity 不是合法 JSON，必须拒绝。"""
        with pytest.raises(json.JSONDecodeError, match="not valid JSON"):
            loads('{"A": Infinity}')

    def test_negative_infinity_rejected(self) -> None:
        """-Infinity 同样必须拒绝。"""
        with pytest.raises(json.JSONDecodeError, match="not valid JSON"):
            loads('{"A": -Infinity}')

    def test_bom_stripped(self) -> None:
        """UTF-8 BOM 应被剥离而不是报错。"""
        assert loads('﻿{"A": 1}') == {"A": 1}

    def test_unquoted_key_rejected(self) -> None:
        """无引号键必须拒绝（与 MaaFramework 行为一致）。"""
        with pytest.raises(json.JSONDecodeError):
            loads("{A: 1}")

    def test_single_quote_rejected(self) -> None:
        """单引号字符串必须拒绝（与 MaaFramework 行为一致）。"""
        with pytest.raises(json.JSONDecodeError):
            loads("{'A': 1}")

    def test_error_reports_line_and_column(self) -> None:
        """语法错误应报出正确的行列号。"""
        text = '{\n  "A": 1,\n  "B": @\n}'
        with pytest.raises(json.JSONDecodeError) as excinfo:
            loads(text)
        message = str(excinfo.value)
        assert "line 3" in message
        assert "column" in message

    def test_error_is_json_decode_error_subclass(self) -> None:
        """抛出的错误必须是 json.JSONDecodeError 子类，兼容既有 except 分支。"""
        with pytest.raises(json.JSONDecodeError):
            loads("{not json")

    def test_duplicate_key_error_has_no_position(self) -> None:
        """重复键无法定位，报错信息不应带误导性的行列号。"""
        with pytest.raises(json.JSONDecodeError) as excinfo:
            loads('{"A": 1, "A": 2}')
        assert "line" not in str(excinfo.value)


# =============================================================================
# pipeline 文件读取
# =============================================================================


@pytest.mark.unit
class TestPipelineFileJsonc:
    """pipeline 文件的 JSONC 读取。"""

    JSONC_TEXT = """
    {
      // 入口节点
      "Start": {
        "recognition": "DirectHit",
        /* 直接命中后点击 */
        "action": "Click",
        "next": ["Next"]
      },
      "Next": {
        "recognition": "DirectHit",
      },
    }
    """

    def test_read_and_validate_pipelines_parses_jsonc(self, tmp_path: Path) -> None:
        """_read_and_validate_pipelines 应能读带注释的 pipeline 文件。"""
        p = tmp_path / "pipeline.json"
        p.write_text(self.JSONC_TEXT, encoding="utf-8")

        out = _read_and_validate_pipelines([str(p)])

        assert len(out) == 1
        data = out[0][1]
        assert set(data) == {"Start", "Next"}
        assert data["Start"]["next"] == ["Next"]

    def test_invalid_jsonc_reports_parse_error(self, tmp_path: Path) -> None:
        """语法错误仍应抛出带文件名的 ValueError。"""
        p = tmp_path / "broken.json"
        p.write_text('{"A": 1 /* 未闭合', encoding="utf-8")

        with pytest.raises(ValueError, match="JSON 解析失败"):
            _read_and_validate_pipelines([str(p)])

    def test_load_pipeline_returns_dict_for_jsonc(self, tmp_path: Path) -> None:
        """load_pipeline 应返回解析后的 dict（注释被剥离）。"""
        fake = tmp_path / "data"
        (fake / "pipelines").mkdir(parents=True)
        p = fake / "pipelines" / "ok.json"
        p.write_text(self.JSONC_TEXT, encoding="utf-8")

        import maa_mcp.paths as paths_module

        original = paths_module.get_data_dir
        paths_module.get_data_dir = lambda: fake
        try:
            out = load_pipeline(str(p))
        finally:
            paths_module.get_data_dir = original

        assert isinstance(out, dict)
        assert set(out) == {"Start", "Next"}


# =============================================================================
# save_pipeline
# =============================================================================


@pytest.mark.unit
class TestSavePipelineJsonc:
    """save_pipeline 的 JSONC 输入。"""

    @pytest.fixture
    def fake_data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake = tmp_path / "data"
        (fake / "pipelines").mkdir(parents=True)
        monkeypatch.setattr("maa_mcp.paths.get_data_dir", lambda: fake)
        monkeypatch.setattr("maa_mcp.pipeline_tools.get_data_dir", lambda: fake)
        return fake

    def test_accepts_jsonc_input(self, fake_data_dir: Path) -> None:
        """带注释的输入字符串应被接受并成功落盘。"""
        text = """
        {
          // 注释
          "A": {"recognition": "DirectHit"},
        }
        """
        out = save_pipeline(text, output_path="a.json")
        assert Path(out).exists()

    def test_writes_standard_json(self, fake_data_dir: Path) -> None:
        """落盘内容应是标准 JSON（注释丢弃），可被标准库 json 读取。"""
        text = '{\n // 注释\n "A": {"recognition": "DirectHit"},\n}'
        out = save_pipeline(text, output_path="b.json")

        content = Path(out).read_text(encoding="utf-8")
        assert "//" not in content
        assert json.loads(content) == {"A": {"recognition": "DirectHit"}}

    def test_invalid_jsonc_reports_error(self, fake_data_dir: Path) -> None:
        """语法错误应返回错误信息而不是抛异常。"""
        out = save_pipeline('{"A": 1 /* 未闭合', output_path="c.json")
        assert isinstance(out, str)
        assert "格式错误" in out


# =============================================================================
# interface.json
# =============================================================================


@pytest.mark.unit
class TestInterfaceJsonJsonc:
    """agent_supervisor 读取带注释的 interface.json。"""

    def test_reads_commented_interface(self, tmp_path: Path) -> None:
        """带注释的 interface.json 应能读到 agent 配置。"""
        (tmp_path / "interface.json").write_text(
            """
            {
              // agent 子进程配置
              "agent": {
                /* 用 python 起 */
                "child_exec": "python",
                "child_args": ["-u", "agent/main.py"],
                "timeout": 30,
              },
            }
            """,
            encoding="utf-8",
        )

        config = load_agent_config(str(tmp_path))

        assert config is not None
        assert config.child_exec == "python"
        assert config.child_args == ["-u", "agent/main.py"]
