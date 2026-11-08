"""JSONC（带注释的 JSON）支持测试 (issue #38)。

覆盖：
- `maa_mcp.jsonc.loads` 单元行为：注释、尾逗号、严格性边界、错误定位
- pipeline 文件读取：`load_pipeline` / `_read_and_validate_pipelines`
- `save_pipeline`：接受带注释的输入，落盘仍是标准 JSON
- `agent_supervisor`：读取带注释的 interface.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from maa_mcp.agent_supervisor import load_agent_config
from maa_mcp.jsonc import _sanitize_surrogates, _strip_comments, loads
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

    def test_overflowing_float_rejected(self) -> None:
        """溢出成 inf 的数字必须拒绝。

        `1e999` 语法合法，但解析结果是 inf，序列化回去会变成非法的
        `Infinity` 字面量——写出来的文件既不是合法 JSON，MaaFramework 的
        严格解析也会失败。
        """
        with pytest.raises(json.JSONDecodeError, match="not a finite number"):
            loads('{"A": {"threshold": 1e999}}')

    def test_surrogate_pair_decoded(self) -> None:
        """`\\uXXXX` 代理对应还原成真实码点（与 meojson 一致）。"""
        assert loads('{"A": "\\ud83d\\ude00"}') == {"A": "\U0001f600"}

    def test_lone_surrogate_rejected(self) -> None:
        """落单的代理字符必须拒绝。

        标准库会接受它，但后续 `json.dumps(...).encode()` 会抛
        UnicodeEncodeError，异常会直接冲出工具调用。
        """
        with pytest.raises(json.JSONDecodeError, match="lone surrogate"):
            loads('{"A": "\\ud800"}')

    def test_non_str_input_raises_type_error(self) -> None:
        """非 str 输入应抛 TypeError。"""
        with pytest.raises(TypeError):
            loads(b'{"A": 1}')  # type: ignore[arg-type]

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

    def test_trailing_garbage_rejected(self) -> None:
        """顶层值之后的尾部垃圾应报错（meojson 同样报错）。"""
        with pytest.raises(json.JSONDecodeError):
            loads('{"A": 1} garbage')

    def test_trailing_comment_accepted(self) -> None:
        """顶层值之后的注释应被忽略（meojson 同样接受）。"""
        assert loads('{"A": 1} // done') == {"A": 1}

    def test_empty_container_comma_rejected(self) -> None:
        """空容器里的逗号是语法错误（MaaFramework 同样拒绝）。

        meojson 的解析循环必须先完成一个键值对才会收尾，所以 `{,}`、`[,]`
        都是错误；这里若放行，MaaMCP 就会接受 MaaFramework 拒绝的文件。
        """
        with pytest.raises(json.JSONDecodeError):
            loads('{"a": {,}}')
        with pytest.raises(json.JSONDecodeError):
            loads('{"a": [,]}')

    def test_leading_comma_rejected(self) -> None:
        """容器开头的逗号是语法错误（MaaFramework 同样拒绝）。"""
        with pytest.raises(json.JSONDecodeError):
            loads('{, "a": 1}')

    def test_trailing_slash_residue_rejected(self) -> None:
        """顶层值之后的 `/x` 残余应报错。

        这是与 meojson 的一处有意偏离：meojson 的 `skip_whitespace` 把「已到输入
        末尾」与「注释起始符非法」混为一谈，会静默丢弃残余；本模块按标准库语义
        报错（方向是更严，不会接受 MaaFramework 拒绝的文件）。
        """
        with pytest.raises(json.JSONDecodeError):
            loads('{"A": 1}/x')

    def test_cr_only_line_comment_matches_meojson(self) -> None:
        """仅用 CR 换行时，行注释会吞掉后续内容——与 meojson 一致。

        meojson 的行注释扫描只认 `\\n`，因此仅用 CR 换行的文件在 MaaFramework
        里同样会被整段吞掉。这里必须保持一致，否则同一份文件在两边解析结果
        不同（MaaFramework 能读、MaaMCP 读不了，或反之）。
        """
        with pytest.raises(json.JSONDecodeError):
            loads('// 注释\r{"A": 1}')
        # 注释在末尾时不影响已解析出的内容
        assert loads('{"A": 1}// 注释\r') == {"A": 1}

    def test_normalized_key_collision_rejected(self) -> None:
        """规范化后撞车的键必须报错，而不是让后来的值静默覆盖。"""
        payload = {chr(0x1F600): 1, chr(0xD83D) + chr(0xDE00): 2}

        with pytest.raises(json.JSONDecodeError, match="Duplicate key"):
            _sanitize_surrogates(payload, "{}")

    def test_comment_stripping_preserves_length(self) -> None:
        """注释替换成等长空白，长度不变（错误位置才对得上原文）。"""
        text = '{\n// 注释\n"A": 1 /* 块注释 */\n}'

        assert len(_strip_comments(text)) == len(text)

    def test_value_error_converted_to_decode_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """json.loads 抛出的 ValueError 必须转成 JsoncDecodeError。

        解释器的整数位数限制（Python 3.10.7+）就是这么抛的——它不是
        JSONDecodeError，会逃出调用方的 `except json.JSONDecodeError` 分支。
        这里用 monkeypatch 模拟，不依赖解释器版本与限制是否生效。
        """

        def boom(*args: object, **kwargs: object) -> None:
            raise ValueError(
                "Exceeds the limit (4300 digits) for integer string conversion"
            )

        monkeypatch.setattr(json, "loads", boom)

        with pytest.raises(json.JSONDecodeError, match="invalid number"):
            loads('{"A": 1}')

    def test_huge_integer_reported_as_decode_error(self) -> None:
        """超大整数应转成 JSONDecodeError，而不是逃出裸 ValueError。

        Python 3.10.7+ 有整数位数限制（默认 4300 位）；3.10.12+ / 3.11.4+ /
        3.12+ 的 json C scanner 会强制执行并抛 ValueError——它不是
        JSONDecodeError，会逃出调用方的 `except` 分支直接冲到工具调用之外。
        """
        setter = getattr(sys, "set_int_max_str_digits", None)
        if setter is None:
            pytest.skip("当前解释器没有整数位数限制")

        # 解释器要求上限至少是 640（0 表示不限制），取最小值再超一点
        old = sys.get_int_max_str_digits()
        setter(640)
        try:
            try:
                loads('{"A": ' + "9" * 700 + "}")
            except json.JSONDecodeError:
                pass  # 期望路径：已转成 JSONDecodeError 子类
            except ValueError as e:
                pytest.fail(f"ValueError 逃出了 JSONDecodeError: {e}")
            else:
                pytest.skip("当前解释器的 json scanner 未强制整数位数限制")
        finally:
            setter(old)

    @pytest.mark.parametrize(
        "payload",
        [
            "{}",
            "[]",
            '{"a": 1}',
            '{"a": [1, 2, {"b": null}]}',
            '{"s": "he said \\"hi\\" // x"}',
            '{"u": "\\u4f60\\u597d"}',
            '{"esc": "a\\\\b"}',
            '{"n": [0, -0, 1e5, 1E5, -1.5e-3, 0.0]}',
            '{"t": true, "f": false, "z": null}',
            '{"nested": {"a": {"b": {"c": [[[1]]]}}}}',
            '{"slash": "a/b"}',
            '{"emoji": "😀 中文"}',
        ],
    )
    def test_matches_stdlib_for_plain_json(self, payload: str) -> None:
        """纯 JSON 的解析结果必须与标准库完全一致（JSON 是 JSONC 的子集）。"""
        assert loads(payload) == json.loads(payload)


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
        """未闭合的块注释应抛出带文件名与 JSONC 具体原因的 ValueError。"""
        p = tmp_path / "broken.json"
        p.write_text('{"A": 1 /* 未闭合', encoding="utf-8")

        with pytest.raises(ValueError, match="JSON 解析失败") as excinfo:
            _read_and_validate_pipelines([str(p)])

        # 断言 JSONC 层的具体原因，避免这条用例在缺少 JSONC 支持时也能通过
        assert "Unterminated comment" in str(excinfo.value)

    def test_duplicate_key_rejected(self, tmp_path: Path) -> None:
        """重复的节点名应报错（meojson 同样拒绝）。"""
        p = tmp_path / "dup.json"
        p.write_text('{"A": {"x": 1}, "A": {"x": 2}}', encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate key"):
            _read_and_validate_pipelines([str(p)])

    def test_non_utf8_file_reports_clear_error(self, tmp_path: Path) -> None:
        """非 UTF-8 编码的文件应给出明确错误，而不是抛出裸异常。"""
        p = tmp_path / "gbk.json"
        p.write_bytes('{"A": "中文"}'.encode("gbk"))

        with pytest.raises(ValueError, match="UTF-8"):
            _read_and_validate_pipelines([str(p)])

    def test_deeply_nested_reports_clear_error(self, tmp_path: Path) -> None:
        """嵌套层级过深应给出明确错误，而不是抛出未捕获的 RecursionError。"""
        p = tmp_path / "deep.json"
        p.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")

        with pytest.raises(ValueError, match="嵌套层级过深"):
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
        # 断言 JSONC 层的具体原因，避免这条用例在缺少 JSONC 支持时也能通过
        assert "Unterminated comment" in out

    def test_overflowing_number_rejected(self, fake_data_dir: Path) -> None:
        """溢出成 inf 的数字应被拒绝——否则落盘会写出非法的 `Infinity`。"""
        out = save_pipeline('{"A": {"threshold": 1e999}}', output_path="d.json")
        assert isinstance(out, str)
        assert "格式错误" in out

    def test_deeply_nested_reports_error(self, fake_data_dir: Path) -> None:
        """嵌套层级过深应返回错误信息，而不是抛出未捕获的 RecursionError。"""
        out = save_pipeline("[" * 2000 + "]" * 2000, output_path="e.json")
        assert isinstance(out, str)
        assert "嵌套层级过深" in out


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

    def test_broken_interface_returns_none(self, tmp_path: Path) -> None:
        """interface.json 解析失败时应返回 None，而不是把异常抛出去。"""
        (tmp_path / "interface.json").write_text(
            '{"agent": {"child_exec": "python"} /* 未闭合',
            encoding="utf-8",
        )

        assert load_agent_config(str(tmp_path)) is None
