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

## AI 工作笔记

给在本项目工作的 AI 助手（Claude Code 等）的提醒。__工具级细节写在工具描述里__——本节只放跨工具的共性经验。

### AI 调工具速度基线（单次完整循环：click→验证→input）

- 最佳：5–10 s/步
- 正常：10–20 s/步
- 异常：>30 s/步 → 自查：是不是在啰嗦/重复验证/等错了东西

提速手段（按收益排序）：

1. 同轮 batch 并行发工具调用
2. 跳过中间验证（click → input 中间不夹 screencap/ocr）
3. 别复述显而易见的事；每个动作一句话结论
4. 不确定__直接问用户__，别自己磨蹭 5 s

### 测试方法论 checklist

下任何结论前先自查：

- [ ] "测什么、控什么变量"——__动手前__就明确
- [ ] 至少 2–3 次重复，单次永远不能下结论
- [ ] 独立信号验证（screencap + ocr 对比），不靠脑补"应该在这"
- [ ] 每步操作后问"是不是我之前的步骤污染了"
- [ ] 反事实思考："下拉可能不 click 也弹吗？""文本可能之前就在那吗？"
- [ ] "API 返回 True" ≠ "动作视觉生效"——永远别只信 API

### MaaMCP 工具行为注记（项目特定）

- __`ocr` / `screencap` 支持 `region=(x,y,w,h)`__——小区域 OCR 提速 4–8×，坐标由 maafw `JOCR(roi=..., roi_offset=...)` 自动补偿（不用 Python 侧手写 crop+offset）。性能数据见 commit `ea4b4bd`。
- __Win32 `click` 对 Chromium/Electron 窗口可能静默失效__——PostMessage 鼠标消息被 Chromium 丢，API 返回 True 但界面无变化。键盘消息（`post_input_text` / `post_key_*`）仍能正常投递（Chrome 内部对键盘消息更宽松）。完整说明在工具描述里。
- __`input_text` 需要目标已聚焦__——它把字符投到当前焦点元素。`click` 失败没建立焦点的话，`input_text` 会投到错误位置。正确顺序：`click(target)` → `input_text(text)` __紧接__，中间不夹 screencap/ocr。
- __Controller 有生命周期__——系统跨天 / Chrome 重启 / 休眠唤醒后，`controller_id` 失效。症状：所有操作静默返回 None/False。处理：重新 `find_window_list()` + `connect_window()`。
- __别把"测试污染"误判成"工具 bug"__——工具用过一次后又失败，先检查测试状态（是否切窗、是否手动操作、controller 是否失效），再怀疑工具本身。
- __`save_captured_image` 写入项目 bundle，不是 MaaMCP 数据目录__——目标是 `<bundle_root>/image/<子分类>/<元素名>.png`（TemplateMatch 的 `template` 字段读取路径）。`bundle_root` 是传给 `Resource.post_bundle()` 的目录：MAAGC 是 `assets/resource/base/`；MaaFramework sample 是 `<repo>/sample/resource`。默认 `overwrite=False` 保护已有模板，更新时显式传 `True`。
- __`benchmark_node` 测的是 wall-clock，不是单节点耗时__——返回的 `latency_ms` 是 `post_task → TaskDetail` 总耗时，含 entry 识别开销（~50-200ms）。想估节点本身耗时减掉这段基线。`mean_score=None` 且 `successes=0` 表示一次都没命中——通常是阈值/ROI/模板漂移的信号。
- __`run_pipeline` / `benchmark_node` 支持单次 `pipeline_override`__——字段级节点覆盖，直接传给 `post_task`（与 interface.json 的 `pipeline_override` 同机制）：单节点验证时收紧 `roi`、调 `expected`/`threshold`/`timeout`，不用改 pipeline 文件。只对单次运行生效——Resource 不被污染。覆盖的节点名若不在已加载文件中会出现在 `warnings`（拼错节点名时覆盖静默不生效，记得检查）。`benchmark_node` 拒绝覆盖 entry/target 的 `next`（会破坏隔离链路）。
- __`run_pipeline` 支持工具侧 `timeout_seconds`__——轮询任务状态，超时调 `post_stop()`，返回 `status="timeout"` + 部分节点详情，不再无限阻塞 MCP 调用（识别不命中的节点会烧满自己的 `timeout`，默认 20s）。注意：MaaFramework 把被 stop 的任务自身 status 标记为 succeeded——工具显式报 `"timeout"`，不信任它。单节点验证建议 5-15s；`None`（默认）保持旧的不限时行为。

### Pipeline 节点调参循环

TemplateMatch / OCR / ColorMatch 节点不稳定时，按这个循环迭代（issue #36 item #4）：

1. `screencap(cid)` → Read 源帧，目视定位目标 region
2. `screencap(cid, region=(x, y, w, h))` → 只裁那块
3. Read 裁图 → 视觉确认是不是预期元素
4. `save_captured_image(cropped_path, bundle_root, subcategory, name)` → 提到 TemplateMatch 模板
5. pipeline JSON 里写：`"recognition": "TemplateMatch", "template": "<subcategory>/<name>.png"`
6. `benchmark_node(cid, pipeline_path, node=<name>, iterations=10..50)` → 看 `mean_score`、`latency_ms`、`all_results_samples`
7. `mean_score < 0.85` 或 `successes < iterations`：收紧 ROI（缩 `region`）、抬高 `threshold`、或重新截一张更准的模板——先用 `pipeline_override={"<name>": {"roi": [...], "threshold": ...}}` 免改文件试参，确定后再把最终值写回 pipeline JSON
8. 重复 2-7 直到稳定

MaaMCP 侧 pipeline infra 集成测试见 `tests/test_dbg_pipeline.py`（标 `@pytest.mark.integration`；缺 `MaaDbgControlUnit` DLL 时自动 skip）。

## 本地化

- [CLAUDE.md](CLAUDE.md)：本文档的英文版

__规则__：更新本文件时，必须同步修改 [CLAUDE.md](CLAUDE.md)。
