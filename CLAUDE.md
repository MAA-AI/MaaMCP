# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MaaMCP is an MCP (Model Context Protocol) server that exposes MaaFramework's automation capabilities to AI assistants. It provides Android device control via ADB and Windows desktop automation via window handles.

## Development Commands

```bash
# Install dependencies in development mode
pip install -e .

# Run MCP server (standard serial mode)
maa-mcp
# or
python -m maa_mcp

# Run MCP server (pipeline mode with background screenshot thread)
maa-mcp-server
# or
python -m maa_mcp.pipeline_server

# Run tests
pytest tests/ -v
pytest tests/test_basic.py -v  # run specific file
```

## Architecture

### Entry Points

The package has multiple entry points defined in `pyproject.toml`:
- `maa-mcp` / `maa_mcp`: Standard MCP server ([__main__.py](maa_mcp/__main__.py))
- `maa-mcp-server` / `maa_mcp_server`: Pipeline server with multi-threaded background monitoring ([pipeline_server.py](maa_mcp/pipeline_server.py))

### Core Components

- __[core.py](maa_mcp/core.py)__: Creates the FastMCP server instance, global registries (`object_registry`, `controller_info_registry`), and `ControllerInfo` dataclass
- __[registry.py](maa_mcp/registry.py)__: `ObjectRegistry` class for managing controller instances by ID
- __[paths.py](maa_mcp/paths.py)__: Cross-platform data directory management using `platformdirs`

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `adb.py` | ADB device discovery (`find_adb_device_list`) and connection (`connect_adb_device`) |
| `win32.py` | Windows window discovery (`find_window_list`) and connection (`connect_window`) |
| `vision.py` | Screen capture (`screencap`) and OCR recognition (`ocr`) |
| `control.py` | Input operations: `click`, `double_click`, `swipe`, `input_text`, `click_key`, `keyboard_shortcut`, `scroll` |
| `resource.py` | Global `Resource`/`Tasker` cache; multi-bundle resource path loading; pipeline node management |
| `download.py` | OCR model file download utilities |
| `agent_supervisor.py` | __Agent 子进程生命周期管理__（MFAAvalonia 风格）：从 `interface.json` 读 agent 配置、TCP 模式启子进程、Windows JobObject 绑定、stdout/stderr 流式日志、`start` / `stop` / `shutdown_all` API（[设计文档](docs/research/custom-action-support.md)） |
| `pipeline_tools.py` | Pipeline protocol docs, single/multi-file `load_pipeline` & `run_pipeline`, `save_pipeline`, `clear_pipeline_resources`, __`stop_pipeline`__（原子地关 Tasker / Agent 子进程 / OCR 循环） |
| `pipeline/` | Pipeline mode state management and logging |

### Two Operation Modes

1. __Serial Mode__: Synchronous execution where each operation waits for the previous to complete
2. __Pipeline Mode__: Multi-threaded mode where a background thread continuously captures screenshots and caches them in a queue for the main thread to process decisions

### Controller Pattern

All device/window control flows through:
1. Discovery functions return device/window identifiers
2. Connection functions create `AdbController` or `Win32Controller` instances (from `maafw`) and register them in `object_registry`
3. Operations use `controller_id` to look up the controller in `object_registry`
4. `controller_info_registry` stores metadata (controller type, connection params) for each `controller_id`

### Key Dependencies

- `maafw>=5.2.6`: Core automation framework (MaaFramework)
- `fastmcp>=2.0.0`: MCP server framework
- `opencv-python>=4.0.0`: Image processing for screenshots
- `loguru>=0.7.0`: Logging
- `platformdirs>=4.0.0`: Cross-platform paths

### Multi-File Pipeline Loading

`run_pipeline(controller_id, pipeline_path)` accepts either a single path (`str`) or a list of paths (`list[str]`). When multiple files are provided, nodes from all files are merged into the same global node table; node `next` references resolve in the merged namespace (cross-file references work).

Conflict handling:

- Default `on_conflict="strict"`: any node name conflict raises an error and writes nothing to the Resource
- `on_conflict="overwrite"`: later file's entire node replaces earlier file's (MaaFramework default); conflicts are reported in `result.warnings`

Loading is atomic: all pre-validation (file existence, JSON validity, conflict detection) must pass before a single `Resource.override_pipeline(merged)` call writes to the Resource. Failures leave the Resource unchanged.

Once loaded, pipeline nodes persist in the Resource until `clear_pipeline_resources()` is called or the process exits.

### Agent Subprocess Lifecycle (Custom Action Support)

For pipelines containing `action: "Custom"` nodes (e.g. MAAGC's `TaskProcessor` / `YearlyTaskProcessor`), MaaMCP spawns a Python agent subprocess to host the custom callbacks, following the MFAAvalonia pattern.

__How it works__:

1. When `run_pipeline` is called with a `resource_path`, the supervisor ([agent_supervisor.py](maa_mcp/agent_supervisor.py)) looks up the chain for an `interface.json` containing an `agent` block.
2. If found, it generates a random 8-char identifier, creates an `AgentClient.create_tcp(0)` (OS-assigned port → identifier `"127.0.0.1:<port>"`), and binds it to the Resource + Controller + Tasker.
3. The agent Python subprocess is launched with the identifier appended as the last CLI argument (MAAGC `agent/main.py:382` reads it via `sys.argv[-1]`).
4. On Windows, the subprocess is bound to a `JobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so a `taskkill /F` on MaaMCP automatically reaps the agent. On POSIX, `start_new_session=True` is used plus an `atexit` fallback.

__Lifecycle contract__:

- `run_pipeline` auto-starts the agent when `start_agent=True` (default).
- `stop_pipeline(controller_id)` atomically stops: OCR loop → `tasker.stop()` → `AgentClient.disconnect()` → subprocess `terminate()` (3s grace) → `kill()` → registry cleanup.
- `atexit` registers `agent_supervisor.shutdown_all()` as a final safety net.

__The agent config schema__ (read from `interface.json`):

```json
{
  "agent": {
    "child_exec": "python",
    "child_args": ["-u", "agent/main.py"],
    "identifier": "optional-fixed-id",
    "timeout": 30,
    "auto_start": true
  }
}
```

__Required MCP tool hygiene__: The AI must call `stop_pipeline(controller_id)` after running a pipeline that auto-started an agent. Otherwise the agent subprocess remains running in the background, holding the TCP port, CPU and memory. JobObject on Windows is a safety net but does not free MaaMCP-side resources (TCP socket, registry entries, loguru file handles).

See [docs/research/custom-action-support.md](docs/research/custom-action-support.md) for the full design rationale and reference to [MFAAvalonia's AgentHelper.cs](https://github.com/MaaXYZ/MFAAvalonia/blob/main/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs).

## Data Storage

OCR models and screenshots are stored in platform-specific directories:

- Windows: `C:\Users\<user>\AppData\Local\MaaMCP\`
- macOS: `~/Library/Application Support/MaaMCP/`
- Linux: `~/.local/share/MaaMCP/`

## Localization

- [CLAUDE_CN.md](CLAUDE_CN.md): Chinese version of this document

__Rule__: When updating this file, always sync changes to [CLAUDE_CN.md](CLAUDE_CN.md)
