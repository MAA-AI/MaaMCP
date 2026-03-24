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

- **[core.py](maa_mcp/core.py)**: Creates the FastMCP server instance, global registries (`object_registry`, `controller_info_registry`), and `ControllerInfo` dataclass
- **[registry.py](maa_mcp/registry.py)**: `ObjectRegistry` class for managing controller instances by ID
- **[paths.py](maa_mcp/paths.py)**: Cross-platform data directory management using `platformdirs`

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `adb.py` | ADB device discovery (`find_adb_device_list`) and connection (`connect_adb_device`) |
| `win32.py` | Windows window discovery (`find_window_list`) and connection (`connect_window`) |
| `vision.py` | Screen capture (`screencap`) and OCR recognition (`ocr`) |
| `control.py` | Input operations: `click`, `double_click`, `swipe`, `input_text`, `click_key`, `keyboard_shortcut`, `scroll` |
| `resource.py` | OCR resource download and tasker management |
| `download.py` | OCR model file download utilities |
| `pipeline/` | Pipeline mode state management and logging |

### Two Operation Modes

1. **Serial Mode**: Synchronous execution where each operation waits for the previous to complete
2. **Pipeline Mode**: Multi-threaded mode where a background thread continuously captures screenshots and caches them in a queue for the main thread to process decisions

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

## Data Storage

OCR models and screenshots are stored in platform-specific directories:
- Windows: `C:\Users\<user>\AppData\Local\MaaMCP\`
- macOS: `~/Library/Application Support/MaaMCP/`
- Linux: `~/.local/share/MaaMCP/`

## Localization

- [CLAUDE_CN.md](CLAUDE_CN.md): Chinese version of this document

__Rule__: When updating this file, always sync changes to [CLAUDE_CN.md](CLAUDE_CN.md)
