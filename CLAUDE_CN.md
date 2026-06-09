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

- __[core.py](maa_mcp/core.py)__：创建 FastMCP 服务器实例、全局注册表（`object_registry`、`controller_info_registry`）和 `ControllerInfo` 数据类
- __[registry.py](maa_mcp/registry.py)__：`ObjectRegistry` 类，用于通过 ID 管理控制器实例
- __[paths.py](maa_mcp/paths.py)__：使用 `platformdirs` 的跨平台数据目录管理

### 模块职责

| 模块 | 用途 |
|------|------|
| `adb.py` | ADB 设备发现（`find_adb_device_list`）和连接（`connect_adb_device`） |
| `win32.py` | Windows 窗口发现（`find_window_list`）和连接（`connect_window`） |
| `vision.py` | 屏幕截图（`screencap`）和 OCR 识别（`ocr`） |
| `control.py` | 输入操作：`click`、`double_click`、`swipe`、`input_text`、`click_key`、`keyboard_shortcut`、`scroll` |
| `resource.py` | 全局 `Resource` / `Tasker` 缓存；多 bundle 资源路径加载；pipeline 节点管理 |
| `download.py` | OCR 模型文件下载工具 |
| `agent_supervisor.py` | __Agent 子进程生命周期管理__（MFAAvalonia 风格）：从 `interface.json` 读 agent 配置、TCP 模式启子进程、Windows JobObject 绑定、stdout/stderr 流式日志、`start` / `stop` / `shutdown_all` API（[设计文档](docs/research/custom-action-support.md)） |
| `pipeline_tools.py` | Pipeline 协议文档、单/多文件 `load_pipeline` 与 `run_pipeline`、`save_pipeline`、`clear_pipeline_resources`、__`stop_pipeline`__（原子地关 Tasker / Agent 子进程 / OCR 循环） |
| `pipeline/` | 流水线模式状态管理和日志 |

### 两种操作模式

1. __串行模式__：同步执行，每个操作等待前一个完成
2. __流水线模式__：多线程模式，后台线程持续截图并缓存在队列中，供主线程处理决策

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

### 多 Pipeline JSON 文件加载

`run_pipeline(controller_id, pipeline_path)` 接受单个路径（`str`）或路径列表（`list[str]`）。多文件场景下，所有文件的节点会被合并到同一全局节点表；节点 `next` 引用在合并后的命名空间内解析（支持跨文件引用）。

冲突处理：

- 默认 `on_conflict="strict"`：检测到任何节点名冲突立即返回错误，不写入 Resource
- `on_conflict="overwrite"`：后加载的整节点覆盖先加载的（MaaFramework 默认行为），冲突节点名会出现在返回的 `result.warnings` 中

加载是原子的：所有预校验（文件存在、JSON 合法、冲突检测）通过后才会调用一次 `Resource.override_pipeline(merged)` 写入 Resource；任何预校验失败都不会污染 Resource。

节点一旦加载会持续驻留到 Resource，直到调用 `clear_pipeline_resources()` 或进程结束。

### Agent 子进程生命周期（Custom Action 支持）

对于含 `action: "Custom"` 节点的 pipeline（如 MAAGC 的 `TaskProcessor` / `YearlyTaskProcessor`），MaaMCP 启动一个 Python agent 子进程来托管自定义回调，对齐 MFAAvalonia 模式。

__工作流程__：

1. `run_pipeline` 收到 `resource_path` 后，supervisor（[agent_supervisor.py](maa_mcp/agent_supervisor.py)）沿父目录链向上查找含 `agent` 块的 `interface.json`。
2. 若找到：随机生成 8 位 identifier，创建 `AgentClient.create_tcp(0)`（系统分配端口 → identifier 为 `"127.0.0.1:<port>"`），并 bind 到 Resource + Controller + Tasker。
3. agent Python 子进程以 identifier 作为最后位置参数启动（MAAGC `agent/main.py:382` 通过 `sys.argv[-1]` 读）。
4. Windows 上：子进程绑到 `JobObject`（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），`taskkill /F` MaaMCP 时 OS 自动连带清理子进程。POSIX 上：`start_new_session=True` + `atexit` 兜底。

__生命周期约束__：

- `run_pipeline` 在 `start_agent=True`（默认）时自动启 agent。
- `stop_pipeline(controller_id)` 原子地：OCR 循环 → `tasker.stop()` → `AgentClient.disconnect()` → 子进程 `terminate()`（3s 宽限）→ `kill()` → 注册表清理。
- `atexit` 注册 `agent_supervisor.shutdown_all()` 作为最后兜底。

__Agent 配置 schema__（从 `interface.json` 读）：

```json
{
  "agent": {
    "child_exec": "python",
    "child_args": ["-u", "agent/main.py"],
    "identifier": "可选固定 id",
    "timeout": 30,
    "auto_start": true
  }
}
```

__MCP 工具使用约束（重要）__：AI 在跑完自动启 agent 的 pipeline 后，__必须__调用 `stop_pipeline(controller_id)` 关闭后台 agent 子进程。否则 agent 会一直占着 TCP 端口、CPU 和内存。Windows JobObject 是兜底，不能释放 MaaMCP 侧资源（TCP socket、注册表项、loguru 文件句柄等）。

详细设计思路与 MFAAvalonia 参考见 [docs/research/custom-action-support.md](docs/research/custom-action-support.md) 和 [MFAAvalonia/AgentHelper.cs](https://github.com/MaaXYZ/MFAAvalonia/blob/main/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs)。

## 数据存储

OCR 模型和截图存储在平台特定的目录中：
- Windows：`C:\Users\<user>\AppData\Local\MaaMCP\`
- macOS：`~/Library/Application Support/MaaMCP/`
- Linux：`~/.local/share/MaaMCP/`