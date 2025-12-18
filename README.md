<!-- markdownlint-disable MD033 MD041 MD024 -->
<p align="center">
  <img alt="LOGO" src="https://cdn.jsdelivr.net/gh/MaaAssistantArknights/design@main/logo/maa-logo_512x512.png" width="256" height="256" />
</p>

<div align="center">

# MaaMCP

![license](https://img.shields.io/github/license/MistEO/MaaMCP)
![activity](https://img.shields.io/github/commit-activity/m/MistEO/MaaMCP?color=%23ff69b4)
![stars](https://img.shields.io/github/stars/MistEO/MaaMCP?style=social)  
[![MaaFramework](https://img.shields.io/badge/MaaFramework-v5-green)](https://github.com/MaaXYZ/MaaFramework)
[![PyPI](https://img.shields.io/pypi/v/maa-mcp?logo=pypi&logoColor=white)](https://pypi.org/project/maa-mcp/)

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的 MCP 服务器
为 AI 助手提供 Android 设备和 Windows 桌面自动化能力

[English](README_EN.md) | 中文

</div>

---

## 简介

MaaMCP 是一个 MCP 服务器，将 MaaFramework 的强大自动化能力通过标准化的 MCP 接口暴露给 AI 助手（如 Claude）。通过本服务器，AI 助手可以：

- 🤖 **Android 自动化** - 通过 ADB 连接并控制 Android 设备/模拟器
- 🖥️ **Windows 自动化** - 控制 Windows 桌面应用程序
  - 🎯 **后台操作** - Windows 上的截图与控制均在后台运行，不占用鼠标键盘，您可以继续使用电脑做其他事情
- 🔗 **多设备协同** - 同时控制多个设备/窗口，实现跨设备自动化
- 👁️ **智能识别** - 使用 OCR 识别屏幕文字内容
- 🎯 **精准操作** - 执行点击、滑动、文本输入、按键等操作
- 📸 **屏幕截图** - 获取实时屏幕截图进行视觉分析

Talk is cheap, 请看: **[🎞️ Bilibili 视频演示](https://www.bilibili.com/video/BV1eGmhBaEZz/)**

## 功能特性

### 🔍 设备发现与连接

- `find_adb_device_list` - 扫描可用的 ADB 设备
- `find_window_list` - 扫描可用的 Windows 窗口
- `connect_adb_device` - 连接到 Android 设备
- `connect_window` - 连接到 Windows 窗口

### 👀 屏幕识别

- `ocr` - 光学字符识别（高效，推荐优先使用）
- `screencap` - 屏幕截图（按需使用，token 开销大）

### 🎮 设备控制

- `click` - 点击指定坐标（支持多触点/鼠标按键选择、长按）
  - Windows 上支持指定鼠标按键：左键、右键、中键
- `double_click` - 双击指定坐标
- `swipe` - 滑动手势（Android 设备滚动/翻页优先使用）
- `input_text` - 输入文本
- `click_key` - 按键操作（支持长按）
  - Android 上可模拟系统按键：返回键(4)、Home键(3)、菜单键(82)、音量键等
  - Windows 上支持虚拟按键码：回车(13)、ESC(27)、方向键等
- `keyboard_shortcut` - 键盘快捷键
  - 支持组合键：Ctrl+C、Ctrl+V、Alt+Tab 等
- `scroll` - 鼠标滚轮（仅 Windows）

### 📝 Pipeline 生成与运行

- `get_pipeline_protocol` - 获取 Pipeline 协议文档
- `save_pipeline` - 保存 Pipeline JSON 到文件（支持新建和更新）
- `load_pipeline` - 读取已有的 Pipeline 文件
- `run_pipeline` - 运行 Pipeline 并返回执行结果

## 快速开始

### 安装方式

#### 方式一：通过 pip 安装（推荐）

```bash
pip install maa-mcp
```

#### 方式二：从源码安装

1. **克隆仓库**

    ```bash
    git clone https://github.com/MistEO/MaaMCP.git
    cd MaaMCP
    ```

2. **安装 Python 依赖**

    ```bash
    pip install -e .
    ```

### 配置客户端

在 Cursor 等软件中，添加 MCP 服务器：

```json
{
  "mcpServers": {
    "MaaMCP": {
      "command": "maa-mcp"
    }
  }
}
```

或

在 Cherry Studio 等软件中，添加 MCP 命令：

```shell
maa-mcp
```

## 使用示例

配置完成后，可以这样使用：

**Android 自动化示例：**

```text
请用 MaaMCP 工具帮我连接 Android 设备，打开美团帮我点一份外卖，我想吃中餐，一人份，20 元左右的
```

**Windows 自动化示例：**

```text
请用 MaaMCP 工具，看看我现在这页 PPT 怎么加一个旋转特效，操作给我看下
```

**Pipeline 生成示例：**

```text
请用 MaaMCP 工具连接我的设备，帮我打开设置，进入显示设置，调整亮度到 50%。
操作完成后，帮我生成这个流程的 Pipeline JSON，以便后续可以直接运行。
```

MaaMCP 会自动：

1. 扫描可用设备/窗口
2. 建立连接
3. 自动下载并加载 OCR 资源
4. 执行识别和操作任务

## 工作流程

MaaMCP 遵循简洁的操作流程，支持多设备/多窗口协同工作：

```mermaid
graph LR
    A[扫描设备] --> B[建立连接]
    B --> C[执行自动化操作]
```

1. **扫描** - 使用 `find_adb_device_list` 或 `find_window_list`
2. **连接** - 使用 `connect_adb_device` 或 `connect_window`（可连接多个设备/窗口，获得多个控制器 ID）
3. **操作** - 通过指定不同的控制器 ID，对多个设备/窗口执行 OCR、点击、滑动等自动化操作

## Pipeline 生成功能

MaaMCP 支持让 AI 将执行过的操作转换为 [MaaFramework Pipeline](https://maafw.xyz/docs/3.1-PipelineProtocol) JSON 格式，实现**一次操作，无限复用**。

### 工作原理

```mermaid
graph LR
    A[AI 执行操作] --> B[操作完成]
    B --> C[AI 阅读 Pipeline 文档]
    C --> D[AI 智能生成 Pipeline]
    D --> E[保存 JSON 文件]
    E --> F[运行验证]
    F --> G{是否成功?}
    G -->|是| H[完成]
    G -->|否| I[分析失败原因]
    I --> J[修改 Pipeline]
    J --> F
```

1. **执行操作** - AI 正常执行 OCR、点击、滑动等自动化操作
2. **获取文档** - 调用 `get_pipeline_protocol` 获取 Pipeline 协议规范
3. **智能生成** - AI 根据文档规范，将**有效操作**转换为 Pipeline JSON
4. **保存文件** - 调用 `save_pipeline` 保存生成的 Pipeline
5. **运行验证** - 调用 `run_pipeline` 验证 Pipeline 是否正常运行
6. **迭代优化** - 根据运行结果分析失败原因，修改 Pipeline 直到成功

### 智能生成的优势

与机械录制不同，AI 智能生成具有以下优势：

- **只保留成功路径**：如果操作过程中尝试了多条路径（如先进入 A 菜单没找到，返回后又进入 B 菜单才找到），AI 会只保留最终成功的路径，去掉失败的尝试
- **理解操作意图**：AI 能够理解每个操作的目的，生成语义清晰的节点名称
- **优化识别条件**：根据 OCR 结果智能设置识别区域和匹配条件
- **验证与迭代**：通过运行验证发现问题，自动修复并增强鲁棒性

### 验证与迭代优化

Pipeline 生成后，AI 会自动进行验证和优化：

1. **运行验证** - 执行 Pipeline 检查是否成功
2. **失败分析** - 如果失败，分析具体哪个节点出错及原因
3. **智能修复** - 常见优化手段：
   - 增加备选识别节点（在 next 列表中添加多个候选）
   - 放宽 OCR 匹配条件（使用正则表达式或部分匹配）
   - 调整 roi 识别区域
   - 增加等待时间（post_delay）
   - 添加中间状态检测节点
4. **重新验证** - 修改后再次运行，直到稳定成功

如果发现 Pipeline 逻辑本身有问题，AI 还可以重新执行自动化操作，结合新旧经验生成更完善的 Pipeline。

### 示例输出

```json
{
  "开始任务": {
    "recognition": "DirectHit",
    "action": "DoNothing",
    "next": ["点击设置"]
  },
  "点击设置": {
    "recognition": "OCR",
    "expected": "设置",
    "action": "Click",
    "next": ["进入显示"]
  },
  "进入显示": {
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

## 注意事项

📌 **Windows 自动化限制**：

- 部分游戏或应用的反作弊机制可能会拦截后台控制操作
- 若目标应用以管理员权限运行，MaaMCP 也需要以管理员权限启动
- 不支持对最小化的窗口进行操作，请保持目标窗口在非最小化状态
- 若默认的后台截图/输入方式不可用（如截图为空、操作无响应），AI 助手可能会尝试切换到前台方式，届时会占用鼠标键盘

## 常见问题

### OCR 识别失败，报错 "Failed to load det or rec" 或提示资源不存在

首次使用时，会自动下载 OCR 模型文件。但可能出现下载失败等情况，请检查数据目录：

- Windows: `C:\Users\<用户名>\AppData\Local\MaaMCP\resource\model\ocr\`
- macOS: `~/Library/Application Support/MaaMCP/resource/model/ocr/`
- Linux: `~/.local/share/MaaMCP/resource/model/ocr/`

1. 检查上述目录中是否有模型文件（`det.onnx`, `rec.onnx`, `keys.txt`）
2. 检查 `model/download.log` 中是否出现资源下载异常
3. 手动执行 `python -c "from maa_mcp.download import download_and_extract_ocr; download_and_extract_ocr()"` 再次尝试下载

### 关于 ISSUE

提交问题时，请提供日志文件，日志文件路径如下：

- Windows: `C:\Users\<用户名>\AppData\Local\MaaMCP\debug\maa.log`
- macOS: `~/Library/Application Support/MaaMCP/debug/maa.log`
- Linux: `~/.local/share/MaaMCP/debug/maa.log`

## 许可证

本项目采用 [GNU AGPL v3](LICENSE) 许可证。

## 致谢

- **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** - 提供强大的自动化框架
- **[FastMCP](https://github.com/jlowin/fastmcp)** - 简化 MCP 服务器开发
- **[Model Context Protocol](https://modelcontextprotocol.io/)** - 定义 AI 工具集成标准
