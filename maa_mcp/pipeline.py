"""
Pipeline 生成支持模块

提供 Pipeline 文档查阅和保存工具，让 AI 能够：
1. 阅读 MaaFramework Pipeline 协议文档
2. 在执行自动化操作后，智能生成 Pipeline JSON
3. 保存生成的 Pipeline 到文件
"""

import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from lzstring import LZString

from maa.tasker import TaskDetail

from maa_mcp.core import mcp
from maa_mcp.paths import get_data_dir
from maa_mcp.resource import get_or_create_resource, get_or_create_tasker


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


@mcp.tool(
    name="load_pipeline",
    description="""
    读取已有的 Pipeline JSON 文件内容。

    参数：
    - pipeline_path: Pipeline JSON 文件路径

    返回值：
    - 成功：返回 Pipeline JSON 内容（dict 格式）
    - 失败：返回错误信息字符串

    说明：
    用于读取已保存的 Pipeline 进行查看或修改，修改后可调用 save_pipeline() 保存。
""",
)
def load_pipeline(pipeline_path: str) -> dict | str:
    path = Path(pipeline_path)
    if not path.exists():
        return f"Pipeline 文件不存在: {pipeline_path}"
    if not path.is_file():
        return f"Pipeline 路径不是文件: {pipeline_path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            pipeline = json.load(f)
        if not isinstance(pipeline, dict):
            return "Pipeline 文件格式错误: 顶层必须是对象"
        return pipeline
    except json.JSONDecodeError as e:
        return f"Pipeline JSON 解析失败: {e}"
    except OSError as e:
        return f"读取文件失败: {e}"


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
    加载并运行 Pipeline JSON 文件。

    参数：
    - controller_id: 控制器 ID，由 connect_adb_device() 或 connect_window() 返回
    - pipeline_path: Pipeline JSON 文件路径
    - entry: 入口节点名称（可选），不指定则使用 Pipeline 中的第一个节点

    返回值：
    - 成功：返回 TaskDetail 对象，包含 task_id、entry、status、nodes 等执行信息
    - 失败：返回错误信息字符串

    说明：
    此函数会先加载 Pipeline 文件到 Resource，然后通过 Tasker 执行任务。
    ⚠️ 重要：run_pipeline 不会自动把界面恢复到入口节点所假设的起始状态。
    运行前请先将设备/窗口切回到 Pipeline 入口对应的起始界面；若无法自动恢复或无法确定当前界面，请提示用户手动恢复后再运行。
""",
)
def run_pipeline(
    controller_id: str,
    pipeline_path: str,
    entry: Optional[str] = None,
) -> TaskDetail | str:
    # 检查文件是否存在
    path = Path(pipeline_path)
    if not path.exists():
        return f"Pipeline 文件不存在: {pipeline_path}"
    if not path.is_file():
        return f"Pipeline 路径不是文件: {pipeline_path}"

    # 获取或创建 Resource 和 Tasker
    resource = get_or_create_resource()
    if not resource:
        return "获取 Resource 失败"

    tasker = get_or_create_tasker(controller_id)
    if not tasker:
        return "获取 Tasker 失败，请确保 controller_id 有效"

    # 加载 Pipeline
    load_job = resource.post_pipeline(str(path.absolute()))
    if not load_job.wait().succeeded:
        return f"加载 Pipeline 失败: {pipeline_path}"

    # 如果没有指定入口节点，尝试从 Pipeline 文件中读取第一个节点
    entry_node: str
    if entry:
        entry_node = entry
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                pipeline_data = json.load(f)
            if isinstance(pipeline_data, dict) and pipeline_data:
                entry_node = next(iter(pipeline_data.keys()))
            else:
                return "Pipeline 文件为空或格式不正确"
        except json.JSONDecodeError as e:
            return f"读取 Pipeline 文件失败: {e}"

    # 执行任务
    task_job = tasker.post_task(entry_node)
    task_detail = task_job.wait().get()

    if not task_detail:
        return "任务执行失败，无法获取执行详情"

    return task_detail


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
