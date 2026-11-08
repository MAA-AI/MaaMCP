"""JSONC（带注释的 JSON）解析。

MaaFramework 内置的 pipeline 解析器基于
[meojson](https://github.com/MistEO/meojson) 的 `json::parsec`，接受带注释与
尾逗号的 JSONC。本模块在 Python 侧复刻同一套语义，使 MaaMCP 能读的文件与
MaaFramework 能读的文件保持一致——否则同一份 pipeline，MaaFramework 加载成功
而 MaaMCP 报 `Expecting value`。

与 MaaFramework（= meojson `json::parsec`）一致的接受集合：

- 允许 `//` 行注释、`/* */` 块注释
- 允许对象 / 数组尾逗号
- 其余严格遵循 JSON 规范：键必须是双引号字符串，拒绝单引号、拒绝
  NaN / Infinity、拒绝重复键、拒绝落单的代理字符（`\\uD800` 之类）

实现走「预处理 + 标准库」而不是自写递归下降解析器：先把注释替换成等长空白
（保留换行），再剥掉尾逗号，最后交给标准库 `json.loads`。空白替换保留了字节
偏移，因此标准库 `JSONDecodeError` 自带的行列号可以直接沿用，无需自己算位置。

已知的三处有意偏离（均因 MaaMCP 与 MaaFramework 的调用方式不同）：

1. **顶层值类型**：meojson 只接受对象 / 数组，本模块的 `loads` 接受任意顶层值
   （是否合法由调用方判断——pipeline 文件另行要求顶层为非空对象）。
2. **溢出数字**：MaaMCP 把解析结果重新序列化成严格 JSON 再交给
   `Resource.override_pipeline`，而 MaaFramework 的解析器走严格 JSON。因此
   `1e999` 这类溢出成 `inf` 的数字在这里被拒绝——否则落盘会写出非法的
   `Infinity` 字面量。
3. **尾部垃圾**：顶层值之后以 `/` 开头的残余内容，meojson 会静默忽略，本模块
   按标准库行为报错。
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

__all__ = ["JsoncDecodeError", "loads"]

_BOM = "﻿"
_WHITESPACE = " \t\r\n"
# 标准库 json 默认接受、但 JSON 规范与 MaaFramework 都不接受的字面量
_NON_JSON_CONSTANTS = frozenset({"NaN", "Infinity", "-Infinity"})


class JsoncDecodeError(json.JSONDecodeError):
    """JSONC 解析错误。

    继承 `json.JSONDecodeError`，既有的 `except json.JSONDecodeError` 分支
    无需修改即可捕获。`pos` 为 `None` 表示该错误无法定位到具体位置（如重复
    键），此时报错信息不带行列号，避免给出误导性的位置。
    """

    def __init__(self, msg: str, doc: str, pos: int | None = None) -> None:
        if pos is None:
            ValueError.__init__(self, msg)
            self.msg = msg
            self.doc = doc
            self.pos = None
            self.lineno = None
            self.colno = None
        else:
            super().__init__(msg, doc, pos)


def _strip_comments(text: str) -> str:
    """把注释替换成等长空白。

    注释内容替换为空格（块注释内的换行保留），返回文本与入参等长，
    行列号完全一致，后续步骤的报错位置可以直接沿用。
    """
    out = list(text)
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        ch = text[i]
        if in_string:
            # 字符串内部：只有 \" 会转义下一个字符，其余按普通字符处理
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "/" and i + 1 < n:
            marker = text[i + 1]
            if marker == "/":
                # 行注释：到行尾为止（换行符本身保留）
                end = text.find("\n", i)
                end = n if end == -1 else end
                for k in range(i, end):
                    out[k] = " "
                i = end
                continue
            if marker == "*":
                # 块注释：到 */ 为止
                end = text.find("*/", i + 2)
                if end == -1:
                    raise JsoncDecodeError("Unterminated comment", text, i)
                for k in range(i, end + 2):
                    if text[k] != "\n":
                        out[k] = " "
                i = end + 2
                continue

        i += 1

    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """把对象 / 数组的尾逗号替换成空格。"""
    out = list(text)
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == ",":
            # 逗号后（跳过空白）紧跟 } 或 ] 即为尾逗号
            j = i + 1
            while j < n and text[j] in _WHITESPACE:
                j += 1
            if j < n and text[j] in "}]":
                out[i] = " "

        i += 1

    return "".join(out)


def _reject_non_json_constants(text: str) -> None:
    """拒绝 NaN / Infinity / -Infinity 字面量。

    标准库 `json` 默认接受这三个字面量，但 JSON 规范与 MaaFramework 都不接受；
    更关键的是它们序列化回 JSON 后仍是 `NaN` 字面量，会让 MaaFramework 的严格
    解析失败，错误信息也难以追溯。
    """
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        # 裸标识符只可能是非法的 JSON 关键字（true / false / null 由标准库处理）
        if ch.isalpha() or (ch == "-" and i + 1 < n and text[i + 1].isalpha()):
            start = i
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_+"):
                j += 1
            token = text[start:j]
            if token in _NON_JSON_CONSTANTS:
                raise JsoncDecodeError(f"{token} is not valid JSON", text, start)
            i = j
            continue

        i += 1


def _make_pairs_hook(doc: str) -> Callable[[list[tuple[str, Any]]], dict[str, Any]]:
    """构造 `object_pairs_hook`：拒绝重复键。

    meojson 遇到重复键会解析失败，标准库则静默取最后一个。这里选择与
    MaaFramework 一致地报错，顺带挡掉「pipeline 文件里节点名写重了」这类
    真实错误。
    """

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise JsoncDecodeError(f"Duplicate key {key!r}", doc)
            result[key] = value
        return result

    return hook


def _make_parse_float(doc: str) -> Callable[[str], float]:
    """构造 `parse_float` 钩子：拒绝溢出成非有限值的数字。

    `1e999` 是合法 JSON 语法，但会被解析成 `inf`，序列化回去就成了非法的
    `Infinity` 字面量——写出来的文件既不是合法 JSON，MaaFramework 的严格
    解析也会失败。标准库默认接受这种写法，这里显式拦掉。
    """

    def parse_float(literal: str) -> float:
        value = float(literal)
        if not math.isfinite(value):
            raise JsoncDecodeError(f"{literal} is not a finite number", doc)
        return value

    return parse_float


def _decode_surrogates(text: str, doc: str) -> str:
    """把 `\\uXXXX` 代理对还原成真实码点，拒绝落单的代理字符。

    标准库 `json` 会把 `"\\ud83d\\ude00"` 解析成两个代理码元（而不是 emoji），
    落单的代理码元（如 `"\\ud800"`）则会让后续 `json.dumps(...).encode()` 抛
    UnicodeEncodeError——MaaFramework 侧的 `override_pipeline` 正是这么调度的，
    异常会直接冲出工具调用。MaaFramework 的 meojson 则把代理对合成真实码点、
    对落单代理直接解析失败，这里与它对齐。

    Raises:
        JsoncDecodeError: 字符串里存在落单的代理字符。
    """
    if not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        return text

    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        code = ord(text[i])
        if 0xD800 <= code <= 0xDBFF:
            # 高位代理：后面必须紧跟低位代理
            if i + 1 < n and 0xDC00 <= ord(text[i + 1]) <= 0xDFFF:
                low = ord(text[i + 1])
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                i += 2
                continue
            raise JsoncDecodeError("lone surrogate in string", doc)
        if 0xDC00 <= code <= 0xDFFF:
            # 低位代理：前面没有配对的高位代理
            raise JsoncDecodeError("lone surrogate in string", doc)
        out.append(text[i])
        i += 1
    return "".join(out)


def _sanitize_surrogates(value: Any, doc: str) -> Any:
    """递归处理解析结果里的字符串（键与值），还原代理对、拒绝落单代理。"""
    if isinstance(value, str):
        return _decode_surrogates(value, doc)
    if isinstance(value, list):
        return [_sanitize_surrogates(item, doc) for item in value]
    if isinstance(value, dict):
        return {
            _decode_surrogates(key, doc): _sanitize_surrogates(item, doc)
            for key, item in value.items()
        }
    return value


def loads(text: str) -> Any:
    """解析 JSONC 文本。

    Args:
        text: JSONC 文本。允许 `//` 与 `/* */` 注释、对象 / 数组尾逗号；
            其余遵循 JSON 规范。

    Returns:
        解析后的 Python 对象。

    Raises:
        TypeError: `text` 不是 str。
        json.JSONDecodeError: 语法错误、重复键、NaN / Infinity 等非法内容。
            错误信息带行列号（重复键与溢出数字除外）。
    """
    if not isinstance(text, str):
        raise TypeError(f"text 必须是 str，实际: {type(text).__name__}")

    # 去掉 UTF-8 BOM，其余偏移量保持不变
    doc = text.removeprefix(_BOM)

    cleaned = _strip_comments(doc)
    _reject_non_json_constants(cleaned)
    cleaned = _strip_trailing_commas(cleaned)

    try:
        parsed = json.loads(
            cleaned,
            object_pairs_hook=_make_pairs_hook(doc),
            parse_float=_make_parse_float(doc),
        )
    except json.JSONDecodeError as e:
        if isinstance(e, JsoncDecodeError):
            raise
        # 用原文重新抛出：偏移量一致，位置信息对用户仍然准确
        raise JsoncDecodeError(e.msg, doc, e.pos) from e

    return _sanitize_surrogates(parsed, doc)
