"""
Pipeline 生成支持模块

提供 Pipeline 文档查阅和保存工具，让 AI 能够：
1. 阅读 MaaFramework Pipeline 协议文档
2. 在执行自动化操作后，智能生成 Pipeline JSON
3. 保存生成的 Pipeline 到文件
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from maa_mcp.core import mcp
from maa_mcp.paths import get_data_dir


# Pipeline 协议文档（精简版，包含 AI 生成 Pipeline 所需的关键信息）
PIPELINE_DOCUMENTATION = '''
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
- `threshold`: double - 置信度阈值，可选，默认 0.3

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
- `timeout`: uint - 识别超时时间（毫秒），默认 20000
- `pre_delay`: uint - 识别到后、执行动作前的延迟（毫秒），默认 200
- `post_delay`: uint - 执行动作后、识别 next 前的延迟（毫秒），默认 200
- `rate_limit`: uint - 识别速率限制（毫秒），默认 1000

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
'''


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
    name="save_pipeline",
    description="""
    保存生成的 Pipeline JSON 到文件。

    参数：
    - pipeline_json: Pipeline JSON 字符串，需符合 MaaFramework Pipeline 协议
    - output_path: 输出文件路径（可选）
      - 如果提供：保存到指定路径
      - 如果不提供：保存到默认位置（用户数据目录/pipelines/）
    - name: Pipeline 名称（可选），用于生成默认文件名

    返回值：
    - 成功：返回保存的文件路径
    - 失败：返回错误信息

    注意事项：
    - pipeline_json 必须是有效的 JSON 格式
    - 生成 Pipeline 时应只保留成功的操作路径，去掉失败的尝试
""",
)
def save_pipeline(
    pipeline_json: str,
    output_path: Optional[str] = None,
    name: Optional[str] = None,
) -> str:
    # 验证 JSON 格式
    try:
        pipeline = json.loads(pipeline_json)
    except json.JSONDecodeError as e:
        return f"Pipeline JSON 格式错误: {e}"

    # 确定输出路径
    if output_path:
        filepath = Path(output_path)
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

    # 确保父目录存在
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 写入文件（格式化输出）
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)

    return f"Pipeline 已保存到: {filepath.absolute()}"
