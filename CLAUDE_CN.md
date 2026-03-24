# CLAUDE_CN.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 项目概述

MaaMCP 是一个 MCP（Model Context Protocol）服务器，将 MaaFramework 的自动化能力暴露给 AI 助手。它通过 ADB 提供 Android 设备控制，通过窗口句柄提供 Windows 桌面自动化。

## 开发命令

```bash
# 以开发模式安装依赖
pip install -e .

# 运行 MCP 服务器（标准串行模式）
maa-mcp
# 或
python -m maa_mcp

# 运行 MCP 服务器（流水线模式，带后台截图线程）
maa-mcp-server
# 或
python -m maa_mcp.pipeline_server

# 运行测试
pytest tests/ -v
pytest tests/test_basic.py -v  # 运行特定文件
```

## 架构

### 入口点

包在 `pyproject.toml` 中定义了多个入口点：
- `maa-mcp` / `maa_mcp`：标准 MCP 服务器（[__main__.py](maa_mcp/__main__.py)）
- `maa-mcp-server` / `maa_mcp_server`：带多线程后台监控的流水线服务器（[pipeline_server.py](maa_mcp/pipeline_server.py)）

### 核心组件

- **[core.py](maa_mcp/core.py)**：创建 FastMCP 服务器实例、全局注册表（`object_registry`、`controller_info_registry`）和 `ControllerInfo` 数据类
- **[registry.py](maa_mcp/registry.py)**：`ObjectRegistry` 类，用于通过 ID 管理控制器实例
- **[paths.py](maa_mcp/paths.py)**：使用 `platformdirs` 的跨平台数据目录管理

### 模块职责

| 模块 | 用途 |
|------|------|
| `adb.py` | ADB 设备发现（`find_adb_device_list`）和连接（`connect_adb_device`） |
| `win32.py` | Windows 窗口发现（`find_window_list`）和连接（`connect_window`） |
| `vision.py` | 屏幕截图（`screencap`）和 OCR 识别（`ocr`） |
| `control.py` | 输入操作：`click`、`double_click`、`swipe`、`input_text`、`click_key`、`keyboard_shortcut`、`scroll` |
| `resource.py` | OCR 资源下载和任务管理 |
| `download.py` | OCR 模型文件下载工具 |
| `pipeline/` | 流水线模式状态管理和日志 |

### 两种操作模式

1. **串行模式**：同步执行，每个操作等待前一个完成
2. **流水线模式**：多线程模式，后台线程持续截图并缓存在队列中，供主线程处理决策

### 控制器模式

所有设备/窗口控制都通过以下流程：
1. 发现函数返回设备/窗口标识符
2. 连接函数创建 `AdbController` 或 `Win32Controller` 实例（来自 `maafw`）并注册到 `object_registry`
3. 操作使用 `controller_id` 在 `object_registry` 中查找控制器
4. `controller_info_registry` 存储每个 `controller_id` 的元数据（控制器类型、连接参数）

### 关键依赖

- `maafw>=5.2.6`：核心自动化框架（MaaFramework）
- `fastmcp>=2.0.0`：MCP 服务器框架
- `opencv-python>=4.0.0`：截图图像处理
- `loguru>=0.7.0`：日志
- `platformdirs>=4.0.0`：跨平台路径

## 数据存储

OCR 模型和截图存储在平台特定的目录中：
- Windows：`C:\Users\<user>\AppData\Local\MaaMCP\`
- macOS：`~/Library/Application Support/MaaMCP/`
- Linux：`~/.local/share/MaaMCP/`