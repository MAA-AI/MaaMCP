"""
PIPELINE_DOCUMENTATION 精简版内容断言

防止精简版回退到过时版本 / 丢失高频能力说明。
精简版必须与上游 MaaFramework docs/zh_cn/3.1-PipelineProtocol.md 保持同步。
"""

import re

import pytest

from maa_mcp.pipeline_tools import PIPELINE_DOCUMENTATION


def test_doc_is_not_empty():
    """精简版不能为空字符串。"""
    assert len(PIPELINE_DOCUMENTATION) > 100
    assert PIPELINE_DOCUMENTATION.strip()


def test_doc_covers_basic_recognition_algorithms():
    """4 种基础识别算法必须保留。"""
    for algo in ["DirectHit", "OCR", "TemplateMatch", "ColorMatch"]:
        assert algo in PIPELINE_DOCUMENTATION, f"缺少基础识别算法说明: {algo}"


def test_doc_covers_basic_actions():
    """9 种基础动作必须保留。"""
    for action in [
        "DoNothing",
        "Click",
        "LongPress",
        "Swipe",
        "Scroll",
        "InputText",
        "ClickKey",
        "StartApp",
        "StopApp",
    ]:
        assert action in PIPELINE_DOCUMENTATION, f"缺少基础动作说明: {action}"


def test_doc_covers_v5_advanced_features():
    """5.x 新增高频能力必须保留在精简版中。

    这些是 MCP 自动化场景下最有可能用到的高级能力，
    精简版应提供最小可用说明，完整用法让用户去查上游文档。
    """
    for feature in ["jump_back", "anchor", "color_filter", "timeout", "on_error"]:
        assert feature in PIPELINE_DOCUMENTATION, f"缺少 5.x 新增能力: {feature}"


def test_doc_covers_composite_recognition():
    """v5.3+ 组合识别（And/Or）必须保留。"""
    # 至少要出现 And 和 Or 这两个识别类型
    assert "And" in PIPELINE_DOCUMENTATION
    assert "Or" in PIPELINE_DOCUMENTATION


def test_doc_references_upstream():
    """精简版必须声明对应的上游版本/链接，便于溯源。"""
    assert "MaaFramework" in PIPELINE_DOCUMENTATION
    assert "github.com/MaaXYZ/MaaFramework" in PIPELINE_DOCUMENTATION


def test_doc_includes_node_attributes_section():
    """节点属性章节必须存在（替代旧的"通用属性"）。"""
    assert "## 节点属性" in PIPELINE_DOCUMENTATION
    # 不应再保留旧的"## 通用属性"标题
    assert "## 通用属性" not in PIPELINE_DOCUMENTATION


def test_doc_includes_advanced_section():
    """进阶能力小节必须存在。"""
    assert "## 进阶能力" in PIPELINE_DOCUMENTATION
    # 进阶小节里要包含 jump_back 和 anchor 的说明
    # 用 ## 完整示例 作为右边界切分（避免被 "### 子节" 中的 "##" 误切）
    after_advanced = PIPELINE_DOCUMENTATION.split("## 进阶能力", 1)[1]
    advanced_section = after_advanced.split("## 完整示例", 1)[0]
    assert "jump_back" in advanced_section
    assert "anchor" in advanced_section.lower() or "Anchor" in advanced_section


def test_doc_includes_best_practices():
    """最佳实践章节不应丢失。"""
    assert "最佳实践" in PIPELINE_DOCUMENTATION


def test_doc_size_within_target():
    """精简版应在 250~400 行之间（控制噪声、保证不缩水）。

    下限防止有人把关键章节删光（原始 214 行 → 目标 250+）。
    上限防止塞入完整版导致 prompt 污染（完整版 ~1600 行）。
    """
    line_count = PIPELINE_DOCUMENTATION.count("\n")
    assert 250 <= line_count <= 400, (
        f"精简版行数 {line_count} 超出目标范围 [250, 400]"
    )


def test_doc_documents_ocr_color_filter():
    """v5.8 OCR.color_filter 是 MCP 用户处理低对比度文字的常用手段，必须说明。"""
    # 至少在 OCR 章节提到 color_filter
    ocr_section = PIPELINE_DOCUMENTATION.split("### OCR")[1].split("###")[0]
    assert "color_filter" in ocr_section


def test_doc_documents_node_contact():
    """v5.0 contact 字段是 Click/Swipe 等动作的核心参数。"""
    click_section = PIPELINE_DOCUMENTATION.split("### Click")[1].split("###")[0]
    assert "contact" in click_section


def test_doc_has_version_marker():
    """精简版应在顶部标注对应的上游 commit（用于日后溯源/同步）。"""
    # 检查是否有形如 6be93eed 或 commit hash 的版本锚点
    assert re.search(r"[0-9a-f]{7,40}", PIPELINE_DOCUMENTATION), (
        "精简版顶部应标注上游 commit hash"
    )
