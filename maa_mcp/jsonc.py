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
  NaN / Infinity、拒绝重复键

实现走「预处理 + 标准库」而不是自写递归下降解析器：先把注释替换成等长空白
（保留换行），再剥掉尾逗号，最后交给标准库 `json.loads`。空白替换保留了字节
偏移，因此标准库 `JSONDecodeError` 自带的行列号可以直接沿用，无需自己算位置。
"""

from __future__ import annotations

import json
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
            错误信息带行列号（重复键除外）。
    """
    if not isinstance(text, str):
        raise TypeError(f"text 必须是 str，实际: {type(text).__name__}")

    # 去掉 UTF-8 BOM，其余偏移量保持不变
    doc = text.removeprefix(_BOM)

    cleaned = _strip_comments(doc)
    _reject_non_json_constants(cleaned)
    cleaned = _strip_trailing_commas(cleaned)

    try:
        return json.loads(cleaned, object_pairs_hook=_make_pairs_hook(doc))
    except json.JSONDecodeError as e:
        if isinstance(e, JsoncDecodeError):
            raise
        # 用原文重新抛出：偏移量一致，位置信息对用户仍然准确
        raise JsoncDecodeError(e.msg, doc, e.pos) from e
