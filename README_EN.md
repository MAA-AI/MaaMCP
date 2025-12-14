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

MCP Server based on [MaaFramework](https://github.com/MaaXYZ/MaaFramework)
Providing Android device and Windows desktop automation capabilities for AI assistants

English | [中文](README.md)

</div>

---

## Introduction

MaaMCP is a Model Context Protocol server that exposes MaaFramework's powerful automation capabilities through standardized MCP interfaces to AI assistants (like Claude). With this server, AI assistants can:

- 🤖 **Android Automation** - Connect and control Android devices/emulators via ADB
- 🖥️ **Windows Automation** - Control Windows desktop applications
  - 🎯 **Background Operation** - Screenshots and controls on Windows run in the background without occupying your mouse or keyboard, allowing you to continue using your computer for other tasks
- 🔗 **Multi-Device Coordination** - Control multiple devices/windows simultaneously for cross-device automation
- 👁️ **Smart Recognition** - Use OCR to recognize on-screen text
- 🎯 **Precise Operations** - Execute clicks, swipes, text input, key presses, and more
- 📸 **Screenshots** - Capture real-time screenshots for visual analysis

Talk is cheap, see: **[🎞️ Bilibili Video Demo](https://www.bilibili.com/video/BV1eGmhBaEZz/)**

## Features

### 🔍 Device Discovery & Connection

- `find_adb_device_list` - Scan available ADB devices
- `find_window_list` - Scan available Windows windows
- `connect_adb_device` - Connect to Android device
- `connect_window` - Connect to Windows window

### 👀 Screen Recognition

- `ocr` - Optical Character Recognition (efficient, recommended for priority use, OCR model auto-downloads on first use)
- `screencap` - Screenshot capture (use as needed, high token cost)

### 🎮 Device Control

- `click` - Click at coordinates (supports multi-touch/mouse button selection, long press)
  - On Windows, supports mouse button selection: left, right, middle
- `double_click` - Double click at coordinates
- `swipe` - Swipe gesture (preferred for scrolling/paging on Android)
- `input_text` - Input text
- `click_key` - Key press (supports long press)
  - On Android, simulates system keys: Back(4), Home(3), Menu(82), Volume keys, etc.
  - On Windows, supports virtual key codes: Enter(13), ESC(27), Arrow keys, etc.
- `keyboard_shortcut` - Keyboard shortcuts
  - Supports key combinations: Ctrl+C, Ctrl+V, Alt+Tab, etc.
- `scroll` - Mouse wheel (Windows only)

## Quick Start

### Installation

#### Option 1: Install via pip (Recommended)

```bash
pip install maa-mcp
```

#### Option 2: Install from source

1. **Clone the repository**

    ```bash
    git clone https://github.com/MistEO/MaaMCP.git
    cd MaaMCP
    ```

2. **Install Python dependencies**

    ```bash
    pip install -e .
    ```

### Configure MCP Clients

Add MCP configuration:

```json
{
  "mcpServers": {
    "MaaMCP": {
      "command": "maa-mcp"
    }
  }
}
```

## Usage Examples

After configuration, you can use it:

**Android Automation Example:**

```text
Please use the MaaMCP tools to connect to my Android device, open Meituan, and help me order a Chinese meal (one portion) around 20 RMB.
```

**Windows Automation Example:**

```text
Please use the MaaMCP tools to show me how to add a rotation animation effect to the current PPT slide, and demonstrate the steps.
```

MaaMCP will automatically:

1. Scan available devices/windows
2. Establish connection
3. Auto-download and load OCR resources (on first use)
4. Execute recognition and operation tasks

## Workflow

MaaMCP follows a streamlined operational workflow with multi-device/window coordination support:

```mermaid
graph LR
    A[Scan Devices] --> B[Establish Connection]
    B --> C[Execute Automation]
```

1. **Scan** - Use `find_adb_device_list` or `find_window_list`
2. **Connect** - Use `connect_adb_device` or `connect_window` (can connect multiple devices/windows, each gets a unique controller ID)
3. **Operate** - Execute OCR, click, swipe, etc. on multiple devices/windows by specifying different controller IDs (OCR resources auto-download on first use)

## Notes

📌 **Windows Automation Limitations**:

- Some games or applications with anti-cheat mechanisms may block background control operations
- If the target application runs with administrator privileges, MaaMCP must also be launched with administrator privileges
- Minimized windows are not supported; please keep the target window in a non-minimized state
- If the default background screenshot/input methods are unavailable (e.g., empty screenshots, unresponsive operations), the AI assistant may attempt to switch to foreground methods, which will occupy the mouse and keyboard

## FAQ

### OCR recognition fails with "Failed to load det or rec" or prompts that resources do not exist

On first use, OCR model files will be automatically downloaded. However, download failures may occur. Please check the data directory:

- Windows: `C:\Users\<username>\AppData\Local\MaaMCP\resource\model\ocr\`
- macOS: `~/Library/Application Support/MaaMCP/resource/model/ocr/`
- Linux: `~/.local/share/MaaMCP/resource/model/ocr/`

1. Check if model files exist in the above directory (`det.onnx`, `rec.onnx`, `keys.txt`)
2. Check for resource download errors in `model/download.log`
3. Manually run `python -c "from maa_mcp.download import download_and_extract_ocr; download_and_extract_ocr()"` to retry downloading

### About Issues

When filing an issue, please provide log files. The log file path is as follows:

- Windows: `C:\Users\<username>\AppData\Local\MaaMCP\debug\maa.log`
- macOS: `~/Library/Application Support/MaaMCP/debug/maa.log`
- Linux: `~/.local/share/MaaMCP/debug/maa.log`

## License

This project is licensed under [GNU AGPL v3](LICENSE).

## Acknowledgments

- **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** - Provides powerful automation framework
- **[FastMCP](https://github.com/jlowin/fastmcp)** - Simplifies MCP server development
- **[Model Context Protocol](https://modelcontextprotocol.io/)** - Defines AI tool integration standards
