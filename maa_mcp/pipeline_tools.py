"""
Pipeline 生成支持模块

提供 Pipeline 文档查阅和保存工具，让 AI 能够：
1. 阅读 MaaFramework Pipeline 协议文档
2. 在执行自动化操作后，智能生成 Pipeline JSON
3. 保存生成的 Pipeline 到文件
"""

import json
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Union
from lzstring import LZString

from loguru import logger

from maa_mcp.core import mcp, object_registry
from maa_mcp.paths import get_data_dir
from maa_mcp.resource import (
    add_resource_path,
    clear_pipelines,
    get_or_create_resource,
    get_or_create_tasker,
)

# Pipeline 协议文档（精简版，包含 AI 生成 Pipeline 所需的关键信息）
PIPELINE_DOCUMENTATION = """
# MaaFramework Pipeline 协议文档

## 概述

Pipeline 是 MaaFramework 的任务流水线，采用 JSON 格式描述，由若干节点（Node）构成。
每个节点包含识别条件和执行动作，节点间通过 next 字段链接形成执行流程。

## 基础结构

```json
{
    "节点名称": {
        "recognition": "识别算法",
        "action": "执行动作",
        "next": ["后续节点1", "后续节点2"],
        // 其他参数...
    }
}
```

## 执行逻辑

1. 从入口节点开始，按顺序检测 next 列表中的每个节点
2. 当某个节点的识别条件匹配成功时，执行该节点的动作
3. 动作执行完成后，继续检测该节点的 next 列表
4. 当 next 为空或全部超时未匹配时，任务结束

## 识别算法类型

### DirectHit
直接命中，不进行识别，直接执行动作。适用于入口节点或确定性操作。

### OCR
文字识别，识别屏幕上的文字。

参数：
- `expected`: string | list<string> - 期望匹配的文字，支持正则表达式
- `roi`: [x, y, w, h] - 识别区域，可选，默认全屏 [0, 0, 0, 0]

示例：
```json
{
    "点击设置": {
        "recognition": "OCR",
        "expected": "设置",
        "roi": [0, 100, 200, 50],
        "action": "Click"
    }
}
```

### TemplateMatch
模板匹配（找图）。

参数：
- `template`: string | list<string> - 模板图片路径（相对于 image 文件夹）
- `roi`: [x, y, w, h] - 识别区域，可选
- `threshold`: double - 匹配阈值，可选，默认 0.7

### ColorMatch
颜色匹配（找色）。

参数：
- `lower`: [r, g, b] | list<[r, g, b]> - 颜色下限
- `upper`: [r, g, b] | list<[r, g, b]> - 颜色上限
- `roi`: [x, y, w, h] - 识别区域，可选

## 动作类型

### DoNothing
什么都不做。常用于入口节点。

### Click
点击操作。

参数：
- `target`: true | [x, y] | [x, y, w, h] | "节点名" - 点击位置
  - true: 点击当前识别到的位置（默认）
  - [x, y]: 固定坐标点
  - [x, y, w, h]: 在区域内随机点击
  - "节点名": 点击之前某节点识别到的位置
- `target_offset`: [x, y, w, h] - 在 target 基础上的偏移，可选

示例：
```json
{
    "点击确认": {
        "recognition": "OCR",
        "expected": "确认",
        "action": "Click",
        "target": true
    }
}
```

### LongPress
长按操作。

参数：
- `target`: 同 Click
- `duration`: uint - 长按时间（毫秒），默认 1000

### Swipe
滑动操作。

参数：
- `begin`: true | [x, y] | [x, y, w, h] | "节点名" - 起始位置
- `end`: true | [x, y] | [x, y, w, h] | "节点名" - 结束位置
- `duration`: uint - 滑动时间（毫秒），默认 200

示例：
```json
{
    "向下滑动": {
        "recognition": "DirectHit",
        "action": "Swipe",
        "begin": [360, 800],
        "end": [360, 400],
        "duration": 300
    }
}
```

### Scroll
鼠标滚轮（仅 Windows）。

参数：
- `dx`: int - 水平滚动距离
- `dy`: int - 垂直滚动距离（正值向上，负值向下，建议使用 120 的倍数）

### InputText
输入文本。

参数：
- `input_text`: string - 要输入的文本

示例：
```json
{
    "输入用户名": {
        "recognition": "DirectHit",
        "action": "InputText",
        "input_text": "admin"
    }
}
```

### ClickKey
按键点击。

参数：
- `key`: int | list<int> - 虚拟按键码
  - Android: 返回键(4), Home(3), 菜单(82), 回车(66)
  - Windows: 回车(13), ESC(27), Tab(9)

### StartApp / StopApp
启动/停止应用（仅 Android）。

参数：
- `package`: string - 包名或 Activity

## 通用属性

- `next`: string | list<string> - 后续节点列表，按顺序尝试识别
- `post_delay`: uint - 执行动作后、识别 next 前的延迟（毫秒），默认 200

## 完整示例

```json
{
    "开始任务": {
        "recognition": "DirectHit",
        "action": "DoNothing",
        "next": ["打开设置"]
    },
    "打开设置": {
        "recognition": "OCR",
        "expected": "设置",
        "action": "Click",
        "next": ["进入显示设置"]
    },
    "进入显示设置": {
        "recognition": "OCR",
        "expected": "显示",
        "action": "Click",
        "next": ["调整亮度"]
    },
    "调整亮度": {
        "recognition": "OCR",
        "expected": "亮度",
        "action": "Swipe",
        "begin": [200, 500],
        "end": [400, 500],
        "duration": 200
    }
}
```

## 生成 Pipeline 的最佳实践

1. **只保留成功路径**：如果在操作过程中尝试了多条路径（如先进入A菜单没找到，又进入B菜单才找到），
   只在 Pipeline 中保留最终成功的路径（B菜单），不要包含失败的尝试（A菜单）。

2. **使用 OCR 识别**：优先使用 OCR 识别文字，这样即使界面布局变化也能正确匹配。

3. **合理设置 ROI**：如果知道目标文字的大致位置，设置 roi 可以提高识别速度和准确性。

4. **节点命名清晰**：使用描述性的节点名称，如"点击设置按钮"、"输入搜索关键词"。

5. **处理等待场景**：如果需要等待页面加载，可以增加 post_delay 或使用中间节点检测加载完成。

6. **链式结构**：确保 next 字段正确链接，形成完整的执行流程。
"""


@mcp.tool(
    name="get_pipeline_protocol",
    description="""
    获取 MaaFramework Pipeline 协议文档。

    在需要生成 Pipeline JSON 时调用此工具，获取 Pipeline 的格式规范和最佳实践。

    返回值：
    - Pipeline 协议的完整文档，包括：
      - 识别算法类型（OCR、TemplateMatch、DirectHit 等）
      - 动作类型（Click、Swipe、InputText 等）
      - 各参数的详细说明
      - 完整示例
      - 生成 Pipeline 的最佳实践

    使用流程：
    1. 完成自动化操作后，调用此工具获取 Pipeline 协议文档
    2. 根据文档规范，将执行过的**有效操作**转换为 Pipeline JSON
    3. 注意：只保留成功路径，去掉失败的尝试和无效步骤
    4. 调用 save_pipeline() 保存生成的 Pipeline
""",
)
def get_pipeline_protocol() -> str:
    return PIPELINE_DOCUMENTATION


# =============================================================================
# 多 Pipeline JSON 文件加载支持
# =============================================================================


class ConflictStrategy(str, Enum):
    """节点冲突处理策略。

    多个 pipeline JSON 文件可能包含同名节点；不同策略决定如何处理这种情况。
    """

    STRICT = "strict"
    """默认。检测到任何节点冲突立即返回错误，不写入 Resource。"""

    OVERWRITE = "overwrite"
    """后加载的整节点覆盖先加载的（与 MaaFramework 多 bundle 加载行为一致）。
    返回的 result.warnings 中会包含冲突节点名。"""


@dataclass(frozen=True)
class PipelineLoadResult:
    """run_pipeline 的统一返回结构。

    字段说明：
    - success: 任务是否成功
    - files: 实际加载的 pipeline 文件绝对路径列表
    - node_count: 合并后写入 Resource 的节点总数
    - entry: 实际入口节点名
    - status: 任务状态字符串
    - task_id: MaaFramework 任务 ID
    - nodes: 节点执行详情列表
    - warnings: 警告信息（如节点冲突等）
    - error: 错误信息（仅在失败时填充）
    """

    success: bool
    files: List[str] = field(default_factory=list)
    node_count: int = 0
    entry: str = ""
    status: str = ""
    task_id: int = 0
    nodes: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        """序列化为 dict，仅包含有值的字段。"""
        result: dict = {"success": self.success}
        if self.files:
            result["files"] = self.files
        if self.node_count:
            result["node_count"] = self.node_count
        if self.entry:
            result["entry"] = self.entry
        if self.status:
            result["status"] = self.status
        if self.task_id:
            result["task_id"] = self.task_id
        if self.nodes:
            result["nodes"] = self.nodes
        if self.warnings:
            result["warnings"] = self.warnings
        if self.error:
            result["error"] = self.error
        return result


def _normalize_paths(pipeline_path: Union[str, List[str]]) -> List[str]:
    """规范化 pipeline_path 输入。

    接受单字符串或字符串列表：
    - str → 包装为单元素 list
    - list → 保持原顺序，过滤空字符串和 None
    - 全空输入 → 返回空 list（不抛错，让调用方决定如何处理）

    Raises:
        TypeError: 输入既非 str 也非 list。
    """
    if isinstance(pipeline_path, str):
        return [pipeline_path] if pipeline_path else []
    if isinstance(pipeline_path, list):
        return [p for p in pipeline_path if p]
    raise TypeError(
        f"pipeline_path 必须是 str 或 list[str]，实际类型: {type(pipeline_path).__name__}"
    )


def _read_and_validate_pipelines(
    files: List[str],
) -> List[tuple[str, dict]]:
    """读取并校验 pipeline JSON 文件。

    校验项：
    1. 文件存在
    2. 路径是文件（不是目录）
    3. JSON 解析成功
    4. 顶层是非空 dict

    Args:
        files: 文件路径列表（绝对或相对路径均可）

    Returns:
        List[(abs_path, dict)]，abs_path 是绝对路径

    Raises:
        ValueError: 任意校验项失败，错误信息含具体文件路径和原因。
    """
    result: List[tuple[str, dict]] = []
    for f in files:
        path = Path(f)
        if not path.exists():
            raise ValueError(f"Pipeline 文件不存在: {f}")
        if not path.is_file():
            raise ValueError(f"Pipeline 路径不是文件: {f}")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"Pipeline JSON 解析失败: {f}: {e}") from e
        if not isinstance(data, dict) or not data:
            raise ValueError(f"Pipeline 文件格式错误（顶层必须是非空对象）: {f}")
        result.append((str(path.absolute()), data))
    return result


def _merge_pipelines(
    file_dicts: List[tuple[str, dict]],
    on_conflict: ConflictStrategy,
) -> tuple[dict, List[str]]:
    """应用层浅合并多个 pipeline dict。

    合并语义：
    - STRICT 模式：检测到任何节点冲突立即抛 ValueError
    - OVERWRITE 模式：后加载的整节点覆盖先加载的，返回去重后的 conflicts 列表

    注意：节点对象是**整节点替换**，不做字段级深合并（与 MaaFramework
    `default_pipeline.json` 行为不同）。

    Args:
        file_dicts: List[(file_path, dict)]
        on_conflict: 冲突处理策略

    Returns:
        (merged_dict, conflicts_list)

    Raises:
        ValueError: STRICT 模式 + 节点冲突。
    """
    merged: dict = {}
    conflicts: List[str] = []
    for fpath, data in file_dicts:
        for name, node in data.items():
            if name in merged:
                if on_conflict == ConflictStrategy.STRICT:
                    raise ValueError(
                        f"Pipeline 节点冲突 {name!r} (在 {fpath} 中)，"
                        f"已加载节点来自更早的文件。如需允许覆盖请使用 on_conflict='overwrite'"
                    )
                conflicts.append(name)
            merged[name] = node
    return merged, sorted(set(conflicts))


def _validate_entry(
    entry: Optional[str],
    merged: dict,
    first_file_dict: dict,
) -> str:
    """校验并解析入口节点名。

    Args:
        entry: 用户显式指定的入口；为 None 时使用首文件第一个 key。
        merged: 合并后的完整节点表。
        first_file_dict: 第一个文件的 dict（用于默认 entry 解析）。

    Returns:
        校验通过的入口节点名。

    Raises:
        ValueError: 显式 entry 不在 merged 中。
    """
    if entry is None:
        # 使用首文件第一个 key（保持与 dict 插入顺序一致）
        return next(iter(first_file_dict.keys()))
    if entry not in merged:
        available = sorted(merged.keys())
        raise ValueError(
            f"入口节点 {entry!r} 不存在于合并后的节点表中，可用节点: {available}"
        )
    return entry


def _parse_task_status(status: Any) -> str:
    """将 MaaFramework TaskDetail.status 映射为可读字符串。"""
    if status.succeeded:
        return "succeeded"
    if status.failed:
        return "failed"
    if status.running:
        return "running"
    if status.pending:
        return "pending"
    if status.done:
        return "done"
    return str(status)


def _build_run_result(
    file_dicts: List[tuple[str, dict]],
    entry_node: str,
    merged: dict,
    task_detail: Any,
    conflicts: List[str],
    on_conflict: ConflictStrategy,
) -> PipelineLoadResult:
    """组装 run_pipeline 的返回结果。"""
    nodes_info: List[dict] = []
    if hasattr(task_detail, "nodes") and task_detail.nodes:
        for node in task_detail.nodes:
            node_info: dict = {}
            if hasattr(node, "recognition") and node.recognition:
                node_info["recognition"] = {
                    "all_results": getattr(node.recognition, "all_results", None)
                }
            if hasattr(node, "name"):
                node_info["name"] = node.name
            nodes_info.append(node_info)

    warnings: List[str] = []
    if on_conflict == ConflictStrategy.OVERWRITE and conflicts:
        warnings = [f"节点冲突（后文件覆盖前文件）: {n}" for n in conflicts]

    return PipelineLoadResult(
        success=bool(task_detail.status.succeeded),
        files=[f for f, _ in file_dicts],
        node_count=len(merged),
        entry=entry_node,
        status=_parse_task_status(task_detail.status),
        task_id=task_detail.task_id,
        nodes=nodes_info,
        warnings=warnings,
    )


@mcp.tool(
    name="load_pipeline",
    description="""
    读取已有的 Pipeline JSON 文件内容（不写入 Resource）。

    参数：
    - pipeline_path: 单个 Pipeline JSON 文件路径（str），或多个路径组成的列表。
                  多文件场景下，按列表顺序读取；返回结构会区分单/多文件。

    返回值：
    - 单文件（str 或 1-元素 list）：返回 Pipeline JSON 内容（dict 格式）
    - 多文件：返回 {abs_path: dict_content} 的映射，key 是文件绝对路径
    - 失败：返回错误信息字符串

    说明：
    - 用于读取已保存的 Pipeline 进行查看或修改，修改后可调用 save_pipeline() 保存。
    - 本工具只做文件 I/O，不触碰 Resource。合并/写入是 run_pipeline 的职责。
""",
)
def load_pipeline(pipeline_path: Union[str, List[str]]) -> dict | str:
    try:
        files = _normalize_paths(pipeline_path)
    except TypeError as e:
        return f"参数错误: {e}"

    if not files:
        return "pipeline_path 至少包含一个文件路径"

    try:
        file_dicts = _read_and_validate_pipelines(files)
    except ValueError as e:
        return f"Pipeline 校验失败: {e}"

    # 单文件（无论是 str 还是 1-元素 list）保持向后兼容：返回内容本身
    if len(file_dicts) == 1:
        return file_dicts[0][1]

    # 多文件：返回 {abs_path: content} 映射
    return {fpath: data for fpath, data in file_dicts}


@mcp.tool(
    name="save_pipeline",
    description="""
    保存 Pipeline JSON 到文件。

    参数：
    - pipeline_json: Pipeline JSON 字符串，需符合 MaaFramework Pipeline 协议
    - output_path: 输出文件路径（可选）
      - 如果提供：保存到指定路径（若文件已存在会被覆盖）
      - 如果不提供：保存到默认位置（用户数据目录/pipelines/）
    - name: Pipeline 名称（可选），用于生成默认文件名
    - overwrite: 是否覆盖已存在的文件，默认 True

    返回值：
    - 成功：返回保存的文件路径
    - 失败：返回错误信息

    说明：
    可用于新建 Pipeline 或更新已有 Pipeline（指定 output_path 为已有文件路径即可覆盖更新）。
""",
)
def save_pipeline(
    pipeline_json: str,
    output_path: Optional[str] = None,
    name: Optional[str] = None,
    overwrite: bool = True,
) -> str:
    # 验证 JSON 格式
    try:
        pipeline = json.loads(pipeline_json)
    except json.JSONDecodeError as e:
        return f"Pipeline JSON 格式错误: {e}"

    # 验证 Pipeline 结构：必须是以节点名为键的非空对象
    if not isinstance(pipeline, dict):
        return (
            "Pipeline JSON 结构错误: 顶层必须是对象（以节点名为键），而不是数组或原始值"
        )

    if not pipeline:
        return "Pipeline JSON 结构错误: 对象不能为空，至少需要包含一个节点配置"

    # 确定输出路径
    if output_path:
        filepath = Path(output_path)
        # 如果指定的路径是目录，则在该目录下生成文件名
        if filepath.is_dir():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if name:
                safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")
                safe_name = safe_name.strip()[:50] or "pipeline"
                filepath = filepath / f"{safe_name}_{timestamp}.json"
            else:
                filepath = filepath / f"pipeline_{timestamp}.json"
    else:
        pipelines_dir = get_data_dir() / "pipelines"
        pipelines_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if name:
            # 清理名称中的非法字符
            safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")
            safe_name = safe_name.strip()[:50] or "pipeline"
            filepath = pipelines_dir / f"{safe_name}_{timestamp}.json"
        else:
            filepath = pipelines_dir / f"pipeline_{timestamp}.json"

    # 检查文件是否已存在
    if filepath.exists() and not overwrite:
        return f"文件已存在且 overwrite=False: {filepath.absolute()}"

    try:
        # 确保父目录存在
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件（格式化输出）
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(pipeline, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return f"写入文件失败: {e}"

    return str(filepath.absolute())


@mcp.tool(
    name="run_pipeline",
    description="""
    加载并运行一个或多个 Pipeline JSON 文件。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - pipeline_path: Pipeline JSON 文件路径（str），或多个路径组成的列表。
                  多文件场景下，按列表顺序合并节点到同一全局节点表；
                  一个文件的节点可以引用另一文件中的节点。
    - entry: 入口节点名称（可选）。不指定时使用 pipeline_path 第一个文件的第一个节点。
    - resource_path: 资源目录路径（可选），用于指定 Pipeline 所需的资源文件路径。
      如果不指定，则使用 MaaMCP 默认的资源目录。
      当 Pipeline 需要使用外部资源（如 MaaGC 的资源）时，需要指定此参数。
    - on_conflict: 节点冲突处理策略
        - "strict" (默认): 检测到任何节点冲突立即返回错误，不写入 Resource
        - "overwrite": 后加载的整节点覆盖先加载的（与 MaaFramework 行为一致），
                       冲突节点名会出现在返回的 warnings 中

    返回值：
    - 成功/失败统一返回 PipelineLoadResult 序列化后的 dict，包含以下字段：
      - success: bool
      - files: 实际加载的文件绝对路径列表
      - node_count: 合并后写入 Resource 的节点总数
      - entry: 入口节点名称
      - status: 执行状态字符串（"succeeded" | "failed" | "running" | "pending" | "done"）
      - task_id: 任务 ID
      - nodes: 节点详情列表
        - name: 节点名称
        - recognition: 识别结果（如果有），包含 all_results 列表
          - all_results: 识别到的目标列表，每项包含 box（坐标）和 score（置信度）
      - warnings: 警告信息（如节点冲突等）
    - 失败/预校验失败：返回错误信息字符串

    判断识别结果：
    - 检查 nodes 是否包含 recognition.all_results：
      - 有内容 = 识别成功，找到了目标
      - 无内容或 nodes 为空 = 识别失败，未找到目标
    - box 格式：[x, y, width, height]
    - score 范围：0-1，越高越准确

    示例返回值（识别成功）：
    {
      "success": true,
      "files": ["main.json", "battle.json"],
      "node_count": 12,
      "entry": "BackButton_500ms",
      "status": "succeeded",
      "task_id": 200000001,
      "nodes": [{
        "name": "BackButton_500ms",
        "recognition": {
          "all_results": [{"box": [653, 7, 46, 40], "score": 0.999726}]
        }
      }]
    }

    说明：
    - 多文件场景下，所有文件的节点会被合并到 Resource 的同一全局节点表；
      节点 `next` 引用在合并后的命名空间内解析（支持跨文件引用）。
    - 加载是原子的：所有预校验（文件存在、JSON 合法、冲突检测）通过后才会写入 Resource。
    - 节点一旦加载会持续驻留到 Resource，直到调用 clear_pipeline_resources() 或进程结束。
    - **Agent 子进程自动启动**（MFAAvalonia 风格）：若 `resource_path` 上级目录的
      `interface.json` 包含 `agent` 块（参考 MAAGC / MFAAvalonia 项目结构），
      run_pipeline 会自动拉起 agent Python 子进程、bind 到 Resource，使
      `action: Custom` / `custom_action: <Name>` 节点能正常执行。
      设 `start_agent=False` 可关闭此行为。
    - **终止 Agent**：Pipeline 跑完后如需关闭后台 agent / Tasker / OCR 循环，
      调用 `stop_pipeline(controller_id)` MCP 工具（原子地按顺序清理，避免孤儿进程）。
    - ⚠️ 重要：run_pipeline 不会自动把界面恢复到入口节点所假设的起始状态。
      运行前请先将设备/窗口切回到 Pipeline 入口对应的起始界面；若无法自动恢复
      或无法确定当前界面，请提示用户手动恢复后再运行。
""",
)
def run_pipeline(
    controller_id: str,
    pipeline_path: Union[str, List[str]],
    entry: Optional[str] = None,
    resource_path: Optional[str] = None,
    on_conflict: str = ConflictStrategy.STRICT.value,
    start_agent: bool = True,
) -> dict | str:
    # 如果传入了 resource_path，添加它以便 get_or_create_resource 加载该路径
    if resource_path:
        add_resource_path(resource_path)

    # 1. 规范化 + 预校验 + 合并（所有失败路径不写入 Resource）
    try:
        files = _normalize_paths(pipeline_path)
    except TypeError as e:
        return f"参数错误: {e}"

    if not files:
        return "pipeline_path 至少包含一个文件路径"

    try:
        file_dicts = _read_and_validate_pipelines(files)
    except ValueError as e:
        return f"Pipeline 校验失败: {e}"

    try:
        strategy = ConflictStrategy(on_conflict)
    except ValueError as e:
        return f"on_conflict 参数错误: {e}（合法值: {[s.value for s in ConflictStrategy]}）"

    try:
        merged, conflicts = _merge_pipelines(file_dicts, strategy)
    except ValueError as e:
        return f"Pipeline 校验失败: {e}"

    # 2. 解析 + 校验 entry
    try:
        entry_node = _validate_entry(entry, merged, file_dicts[0][1])
    except ValueError as e:
        return f"入口节点校验失败: {e}"

    # 3. 获取或创建 Resource 和 Tasker
    resource = get_or_create_resource()
    if not resource:
        return "获取 Resource 失败"

    # 4. 原子写入：单次 override_pipeline
    if not resource.override_pipeline(merged):
        return (
            "override_pipeline 写入失败，Resource 状态可能已变更，"
            "建议调用 clear_pipeline_resources() 重置后再试"
        )

    tasker = get_or_create_tasker(controller_id)
    if not tasker:
        return "获取 Tasker 失败，请确保 controller_id 有效"

    # 5. 自动启动 Agent 子进程（若 resource_path 指向的项目有 interface.json 声明）
    if start_agent:
        from maa_mcp.agent_supervisor import start as start_agent_supervised

        controller = object_registry.get(controller_id)
        try:
            agent_ctx = start_agent_supervised(
                resource_path=resource_path,
                controller_id=controller_id,
                resource=resource,
                controller=controller,
                tasker=tasker,
            )
        except Exception as e:  # noqa: BLE001
            return f"Agent 启动失败: {e}（可设 start_agent=False 跳过）"
        if agent_ctx is not None:
            logger.info(
                f"Agent 已就绪: controller_id={controller_id} "
                f"identifier={agent_ctx.identifier}"
            )

    # 6. 执行任务
    task_job = tasker.post_task(entry_node)
    task_detail = task_job.wait().get()

    if not task_detail:
        return "任务执行失败，无法获取执行详情"

    # 7. 组装结果
    return _build_run_result(
        file_dicts=file_dicts,
        entry_node=entry_node,
        merged=merged,
        task_detail=task_detail,
        conflicts=conflicts,
        on_conflict=strategy,
    ).to_dict()


"""
MPE 相关配置
"""

# MPE 分享协议版本
MPE_SHARE_VERSION = 1
# URL 参数名
MPE_SHARE_PARAM = "shared"
# 默认 MPE 基准地址
MPE_BASE_URL = "https://mpe.codax.site/stable"
# URL 最大大小限制
MPE_MAX_URL_SIZE = 60 * 1024  # 60KB


def generate_share_link(pipeline_obj: dict) -> str:
    # 生成分享链接
    payload = {
        "v": MPE_SHARE_VERSION,
        "d": pipeline_obj,
    }
    json_string = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lz = LZString()
    compressed = lz.compressToEncodedURIComponent(json_string)
    share_url = f"{MPE_BASE_URL}?{MPE_SHARE_PARAM}={compressed}"
    return share_url


@mcp.tool(
    name="open_pipeline_in_browser",
    description="""
    通过浏览器打开 Pipeline JSON 可视化界面。

    参数：
    - pipeline_file_path: Pipeline JSON 文件的本地路径（字符串）

    功能说明：
    该工具会读取指定路径的 Pipeline JSON 文件，将数据压缩编码后生成一个分享链接，
    并自动在系统默认浏览器中打开，方便用户可视化查看工作流结构。

    注意：
    - 此工具无返回值，仅执行打开浏览器的操作
    - 仅在用户要求查看 Pipeline 可视化流程图时使用
    - 传入的文件路径必须指向一个有效的本地 JSON 文件
    - 如果生成的 URL 超过 60KB，将返回错误提示而不打开浏览器
    """,
)
def open_pipeline_in_browser(pipeline_file_path: str) -> None:
    # 读取文件内容
    file_path = Path(pipeline_file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Pipeline 文件不存在: {pipeline_file_path}")
    if not file_path.is_file():
        raise ValueError(f"路径不是文件: {pipeline_file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        pipeline_obj = json.load(f)

    # 生成分享链接
    share_url = generate_share_link(pipeline_obj)

    # 检查 URL 大小
    url_size = len(share_url.encode("utf-8"))
    if url_size > MPE_MAX_URL_SIZE:
        size_kb = url_size / 1024
        raise ValueError(
            f"生成的分享链接过大（{size_kb:.2f} KB），请自行通过复制或文件的方式导入 Pipeline 至 MPE。"
        )

    webbrowser.open(share_url)


@mcp.tool(
    name="clear_pipeline_resources",
    description="""
    清空 Resource 中已加载的 pipeline 节点。

    使用场景：
    - run_pipeline 加载多文件 pipeline 后，希望切换到另一组 pipeline
    - 当前 Resource 中的节点对下次 run_pipeline 造成干扰

    实现说明：
    maafw Resource 不支持"只清 pipeline 节点、保留 bundle 资源"的精确操作。
    本工具走"重置整个 Resource + 下次 run_pipeline 时重新加载所有 resource paths"路径。
    通过 add_resource_path() / run_pipeline(resource_path=...) 配置的资源路径
    在清空后会自动重新加载。

    返回值：
    - 成功：{"cleared": true, "remaining_pipeline_nodes": 0, "resource_paths_will_reload": [...]}
    - 失败：返回错误信息字符串
""",
)
def clear_pipeline_resources() -> dict | str:
    """清空已加载的 pipeline 节点。"""
    from maa_mcp.resource import _resource_paths, get_pipeline_node_count

    paths_snapshot = list(_resource_paths)
    try:
        clear_pipelines()
    except Exception as e:
        return f"清空 Resource 失败: {e}"

    return {
        "cleared": True,
        "remaining_pipeline_nodes": 0,
        "resource_paths_will_reload": paths_snapshot,
        "note": (
            "Resource 已重置。下次 run_pipeline 时会按 resource_paths 顺序重新加载。"
            "如需让已加载的 pipeline 永久生效，请再次调用 run_pipeline()。"
        ),
    }


@mcp.tool(
    name="stop_pipeline",
    description="""
    停止指定 controller 的所有后台资源（Tasker / Agent 子进程 / OCR 循环），
    原子地按顺序清理，避免后台线程/进程脱离成为孤儿。

    关闭顺序（保证不留孤儿）：
    1. pipeline_state.stop() — 停 OCR 截图循环（若在跑）
    2. tasker.stop() — 终止 MaaFramework 后台任务
    3. AgentClient.disconnect() — 优雅通知 agent 子进程退出
    4. 子进程 terminate() → 等 3s → kill()
    5. Windows JobObject 兜底（任何逃逸的子进程都会被 OS 杀掉）
    6. 从 ObjectRegistry 注销 controller / tasker

    参数：
    - controller_id: 要关闭的 controller ID

    返回值：
    - 成功：dict 包含每步执行状态（tasker_stopped / ocr_loop_stopped /
      client_disconnected / process_killed / registry_cleaned）
    - 失败：字符串错误信息
    """,
)
def stop_pipeline(controller_id: str) -> dict | str:
    if not controller_id:
        return "参数错误: controller_id 不能为空"
    if not isinstance(controller_id, str):
        return f"参数错误: controller_id 必须是 str，实际类型: {type(controller_id).__name__}"

    from maa_mcp.agent_supervisor import stop as stop_agent_supervised
    from maa_mcp.pipeline.state import get_pipeline_state

    # 1) OCR loop（若在跑）
    state = get_pipeline_state()
    ocr_running = state.is_running and state.controller_id == controller_id
    if ocr_running:
        try:
            state.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"pipeline_state.stop 失败: {e}")

    # 2) Agent + Tasker（agent_supervisor.stop 内部处理 Tasker）
    result = stop_agent_supervised(controller_id, grace_seconds=3.0)
    result["ocr_loop_stopped"] = ocr_running
    return result
