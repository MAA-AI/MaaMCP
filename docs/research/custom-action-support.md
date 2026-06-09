# 调研报告：MaaMCP `run_pipeline` 对 Custom Action 的支持

> 用户诉求：当前 MaaMCP 的 `run_pipeline` 只能跑"纯节点"Pipeline，遇到 `action: Custom` + `custom_action: TaskProcessor` 这种依赖外部 Python 回调的节点就会卡死。需要让 MaaMCP 在跑 Pipeline 时能把 Custom 节点交给已有的 Python 业务代码（MAAGC 的 `agent/main.py`）去执行。
>
> 调研对象：[F:\workspace\MAAGC\assets\resource\base\pipeline\auto_task.json](F:/workspace/MAAGC/assets/resource/base/pipeline/auto_task.json)、[F:\workspace\MAAGC\agent\action\fight\fight_processor.py](F:/workspace/MAAGC/agent/action/fight/fight_processor.py)、MaaFramework 5.10.5 Python 绑定、MFAAvalonia 参考实现。

## 1. 现状（实测代码）

### 1.1 MaaMCP 侧的缺口

[maa_mcp/pipeline_tools.py:445-535](maa_mcp/pipeline_tools.py#L445-L535) 的 `run_pipeline` 流程只有三步：

```python
resource = get_or_create_resource()           # 1) 创建/复用 Resource
tasker   = get_or_create_tasker(controller_id) # 2) 创建/复用 Tasker（内部 bind(controller)）
resource.post_pipeline(str(path.absolute()))   # 3) 加载 pipeline
tasker.post_task(entry_node)                   # 4) 跑任务
```

它从未：
- 启动 `AgentServer`（没有 `import`）
- 创建 `AgentClient` 与 Resource 绑定（`grep -r AgentClient` 在 MaaMCP 仓库零结果）
- 也没有提供注册 `CustomAction` 子类的入口

所以当 Pipeline 跑到 `"action": "Custom"`, `"custom_action": "TaskProcessor"` 时，Framework 在 C++ 端查 Custom Action 表查不到该名称，节点会直接失败（hit 不到，task 进入"动作未找到"分支）。

### 1.2 MaaFramework 的 Custom Action 机制（Python 绑定层）

读 [C:\Users\29046\AppData\Local\Programs\Python\Python310\lib\site-packages\maa\custom_action.py](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/custom_action.py) 和 [resource.py](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/resource.py) 可以看到，注册自定义动作有两种途径：

| 方式 | 入口 | 适用场景 | 关键 API |
|---|---|---|---|
| **进程内注册** | `Resource.register_custom_action(name, action)` | 自定义动作和 MaaFW 在同一进程 | `MaaResourceRegisterCustomAction(handle, name, c_callback, c_arg)` |
| **跨进程注册** | `AgentServer.custom_action(name)` / `AgentClient(identifier).bind(resource)` | 自定义动作跑在独立 Python 进程 | `MaaAgentServerRegisterCustomAction` + `MaaAgentClientBindResource`（IPC：AF_UNIX socket / TCP fallback） |

**关键约束**（[agent_client.py:256-257](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/agent_client.py#L256-L257)）：

```python
if Library.is_agent_server():
    raise RuntimeError("AgentClient is not available in AgentServer.")
```

也就是说同一个 Python 进程要么当 Server 要么当 Client，二者不能共存。

### 1.3 MAAGC 侧的实际写法

[F:\workspace\MAAGC\agent\main.py:369-389](F:/workspace/MAAGC/agent/main.py#L369-L389) 启动了一个 **AgentServer 进程**：

```python
from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

Toolkit.init_option("./")
socket_id = sys.argv[-1] if len(sys.argv) > 1 else "default_socket_id"
AgentServer.start_up(socket_id)   # 监听 socket_id
AgentServer.join()
```

而各个 Custom 动作（[fight_processor.py:318-384](F:/workspace/MAAGC/agent/action/fight/fight_processor.py#L318-L384)）通过装饰器挂到 Server：

```python
@AgentServer.custom_action("TaskProcessor")
class TaskProcessor(CustomAction):
    def run(self, context, argv):
        ...
        context.run_task("Map_MoveMainCityLeft")
        context.tasker.controller.post_click(x, y).wait()
        ...
        return CustomAction.RunResult(success=True)
```

也就是说，MAAGC 提供的不是"资源文件"而是"两个东西必须同时在场"：
- 一份 pipeline JSON（用 `custom_action: TaskProcessor` 这种名字引用）
- 一个在另一个进程（或将来同一个进程）里监听了 `socket_id` 的 Python `AgentServer`

MAAGC 自己的 GUI（NiceGUI）走 [MaaDebugger](F:/workspace/MAAGC/.venv/Lib/site-packages/MaaDebugger/maafw/__init__.py#L208-L230) 的模式创建 `AgentClient(identifier)` 并 `bind(resource)`，然后 Pipeline 跑到 Custom 节点时 Framework 自动把调用 IPC 转发给 `AgentServer`，执行完返回 bool。

## 2. 实现差距 (Gap)

| 能力 | MaaMCP 现状 | 跑 MAAGC Pipeline 所需 |
|---|---|---|
| 创建 Resource/Tasker | ✅ | ✅ |
| 加载外部 pipeline | ✅ | ✅ |
| 解析 / 保存 / 校验 pipeline | ✅ | ✅ |
| **创建 AgentClient 并 bind 到 Resource** | ❌ | ✅ |
| **管理 socket_id（生成 / 复用 / 转发给 Server 进程）** | ❌ | ✅ |
| **启动 / 拉起外部 Agent 进程** | ❌ | ✅（可选，MAAGC 已有自己的 agent/main.py） |
| **支持用户从 MCP 工具调用动态注册 Custom 回调** | ❌ | ⛳ 加分项（不必须） |

## 3. 推荐方案设计

### 3.1 方案 A：纯客户端模式（MaaMCP 只连外部 Server）— 最小改动，推荐先行

**思路**：MaaMCP 自己永远不实现 Custom 动作；它只负责在跑 Pipeline 前，先用 `AgentClient(socket_id)` 绑到 Resource 上，等外部 Server 接进来。Server 进程（MAAGC 的 `agent/main.py`）由用户或 MAAGC 自己的入口负责启动。

**改动点**（只动 [maa_mcp/pipeline_tools.py](maa_mcp/pipeline_tools.py) 和 [maa_mcp/resource.py](maa_mcp/resource.py)）：

1. 新增 `maa_mcp/agent.py`，提供：
   ```python
   from maa.agent_client import AgentClient
   from maa.resource import Resource

   class AgentClientManager:
       """按 (resource_handle, socket_id) 缓存 AgentClient 实例"""
       def get_or_create(self, resource: Resource, identifier: str) -> AgentClient: ...
       def connect(self, client: AgentClient) -> bool: ...
       def disconnect_all(self) -> None: ...
   ```
2. `run_pipeline` 新增参数 `agent_identifier: Optional[str] = None`：
   - 若非空：创建 `AgentClient(identifier)`，调 `bind(resource)`，再 `connect()`（阻塞等待 Server 接入，set timeout）。
   - 若为空：扫 pipeline JSON 看有没有 `custom_action` 字段（递归扫所有节点），有就报错要求传 identifier；没有就跳过。
3. 退出 / 切换 controller 时调用 `disconnect()` 并释放 handle（要防 GC，参考 [resource.py:55-56](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/resource.py#L55-L56) 那种 holder 模式）。
4. Resource 替换成带 sink 转发版本（参考 MaaDebugger `AgentClient(...).register_sink(resource, controller, tasker)`），把事件转发给 Server，让 MAAGC 那边 `Context` 拿到的 `tasker.controller` 跟 MaaMCP 这边一致（[MaaDebugger __init__.py:259-262](F:/workspace/MAAGC/.venv/Lib/site-packages/MaaDebugger/maafw/__init__.py#L259-L262) 的做法）。

**优点**：
- 改动小（< 200 行）
- 不破坏 MaaMCP 现有架构和已有 Pipeline 流程
- 直接复用 MAAGC 已经在跑的 `agent/main.py`，Server 端零改动
- 可以跑 `Auto_FightTask` / `Auto_YearlyTask` 等所有依赖 Custom 的任务

**缺点**：
- 需要用户先确保 MAAGC agent 进程在跑（`python agent/main.py <socket_id>`），并把同一个 `socket_id` 传进 MCP 工具
- 第一次 `connect()` 会阻塞等 Server，必须设 timeout

### 3.2 方案 B：MaaMCP 自带 in-process AgentServer 进程 — 一体化体验

**思路**：MaaMCP 启动时顺便拉起一个常驻 Python 子进程跑用户的 agent 入口脚本（默认 `python -m maa_mcp.agent_runtime`），子进程内 `AgentServer.start_up(socket_id)`；MaaMCP 这边用 `AgentClient` 连自己起的子进程。

**改动点**：
- 新增 `maa_mcp/agent_runtime.py`：空壳，import 用户传入的 agent 模块（默认从 `resource_path` 的 sibling `agent/main.py` 猜），再 `AgentServer.start_up(socket_id)`。
- 进程管理：`subprocess.Popen` + `atexit` 清理 + 启动日志/重启策略。
- 通信：用一个共享的 `socket_id`（AF_UNIX），Windows 老版本 fallback TCP（Python 绑定已经处理好，参考 [agent_client.py:22-56](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/agent_client.py#L22-L56)）。

**优点**：用户感知不到 socket 概念，"开箱即用"。
**缺点**：复杂度↑、要管进程生命周期、要处理 agent 入口发现（不同工程目录结构不同），目前 yolo。

**建议**：方案 A 先做出来跑通业务，方案 B 在 A 稳定后再迭代。

### 3.3 方案 C：让大模型在 MCP 工具调用里直接写 CustomAction 回调（不推荐）

允许用户注册一个 `CustomAction` 子类，把回调函数挂在 Resource 上。问题：
- MCP 工具调用是 request-response 模型，回调是 long-running + 反向调用（context.run_task 跑回去），二者协议不匹配，会需要把 CustomAction 跑在 worker thread，把 context/tasker/controller 序列化传给大模型。复杂度爆炸。
- MAAGC 已经把全部 CustomAction 业务代码写好了，没必要再写一份。

除非出现"用户就是没有外部 agent 进程、必须现场写一个最小 Stub"的场景，否则不做。

### 3.4 参考实现：MFAAvalonia 的做法（[F:/workspace/MFAAvalonia](F:/workspace/MFAAvalonia)）

> 读 [AgentHelper.cs:75-391](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L75-L391) 和 [MaaProcessor.cs:1576-1610](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/MaaProcessor.cs#L1576-L1610) 的总结。这是 maa 通用 GUI 的事实标准做法。

**核心发现**：

1. **MFAAvalonia 走的是"自启 agent 子进程"模式**（方案 B），不是"连外部 Server"模式（方案 A）：
   - 项目在 `interface.json` 的 `agent` 字段声明要拉起的子进程（参考 [MaaInterface.cs:954-966](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/MaaInterface.cs#L954-L966)）：
     ```json
     {
       "agent": [{
         "child_exec": "python",          // Python 解释器路径
         "child_args": ["agent/main.py"], // agent 入口脚本
         "identifier": "my_id",           // 可选，留空则随机生成 8 位
         "timeout": 30                    // 等待连接秒数
       }]
     }
     ```

   - MaaProcessor 加载完 tasker 后**立刻**调 `AgentHelper.StartAgentsAsync(tasker, agentConfigs, ...)` 拉起所有 agent。

2. **C# 绑定的高阶 API `LinkStart(method, token)`** 一行完成：创建 client + 派生 `AgentServerStartupMethod` 回调（subprocess.Popen 入口） + 等 connect 成功 + 注册 sink。**Python 绑定没有 `LinkStart`**，需要手动分 4 步：
   ```python
   client = AgentClient.create_tcp(0)          # 或 AgentClient(identifier)
   client.bind(resource)                        # 绑到 resource
   client.register_sink(resource, controller, tasker)  # 转发事件
   proc = subprocess.Popen([python, script, client.identifier])  # 启 server
   client.set_timeout(30_000)                   # 30s 超时
   client.connect()                             # 阻塞等 server dial 进来
   ```

3. **AF_UNIX ↔ TCP 模式可切换**：MaaMCP 默认走 TCP（`create_tcp(0)` 让系统分配端口），identifier 自动变成 `"127.0.0.1:<port>"`，Server 端 `start_up("127.0.0.1:port")` 直连。这样在 Windows 老版本和 Linux/macOS 行为一致，**避免** AF_UNIX 路径泄漏 / 命名空间冲突。

4. **3 次重试 + 全杀重启**（[AgentHelper.cs:239-317](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L239-L317)）：LinkStart 失败时杀掉旧子进程 + 重建 client + 重启子进程 + 重 connect。如果 SEHException（C++ 侧偶发）就只重建不报。

5. **子进程生命周期绑定（Windows 特有）**：[AgentHelper.cs:731-786](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L731-L786) 用 Win32 `CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，让 MaaMCP 进程死掉时 agent 子进程自动被 kill，避免僵尸进程。MaaMCP 也要补这段，**特别是在 Windows 上**。

6. **stdout / stderr 流式日志**：agent 子进程的输出要流式接回来打到 MaaMCP 的 logger。Python 用 `subprocess.Popen` 时把 stdout/stderr 设为 PIPE，起两个 daemon 线程读。

7. **`PI_*` 环境变量协议**（[AgentHelper.cs:393-405](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L393-L405)）：MFAAvalonia 把 instance_id / controller snapshot / resource snapshot 通过 env vars 传给子进程。**MaaMCP 不一定要兼容这个协议**（MAAGC 当前没读这些），但可以保留扩展点。

8. **超时与断开**：`LinkStop()` 先 LinkStop client 再 kill 进程（[AgentHelper.cs:538-622](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L538-L622)），顺序很重要：先让 server 优雅退出，再杀进程。

**结论**：MFAAvalonia 的设计是"一站式"——GUI 自己声明要拉什么 agent、自己拉、自己绑。**对 MaaMCP 来说，方案 A（纯客户端）能解决 80% 痛点，方案 B 是"完整体验"目标**。建议两期：

- **第一期（方案 A）**：只加 `run_pipeline(agent_identifier=...)`，要求用户自己起 agent。**先打通业务**。
- **第二期（方案 B）**：参照 MFAAvalonia 模式，加 `agents` 配置 + 自动拉起。这条参考 [AgentHelper.cs](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs) 的全套：retries、JobObject 绑定、stdout/stderr 流式、PI_* env。

## 4. 落地清单

### 4.1 第一期：方案 A（最小改动）

- [ ] **新文件** [maa_mcp/agent.py](maa_mcp/agent.py)：封装 `AgentClientManager`，提供
  - `get_or_create(resource, controller, tasker, identifier) -> AgentClient`
  - `disconnect_all()`（atexit 调）
  - 按 `(resource._handle id, identifier)` 缓存
- [ ] **改 [maa_mcp/pipeline_tools.py](maa_mcp/pipeline_tools.py)** `run_pipeline`：
  - 新增参数 `agent_identifier: Optional[str] = None`
  - 加载 pipeline 后扫描 `custom_action` 字段，缺 identifier 时明确报错
  - 拿到 `resource` 后用 `agent_manager.get_or_create(...)` 然后 `connect()` 阻塞等 server
  - 任务结束 `disconnect()`（**不销毁** client，缓存给下次复用）
- [ ] **改 [maa_mcp/core.py](maa_mcp/core.py) 的 mcp 描述**：在 Pipeline 工具说明里加 "需自行启动 agent 进程并传相同 identifier"
- [ ] **测试**：拿 MAAGC `auto_task.json` 跑 `Auto_FightTask` / `Auto_YearlyTask`，确认节点能 hit + success
- [ ] **清理**：`atexit` 调 `disconnect_all()`（防 socket 文件泄漏，特别是 AF_UNIX 模式）

### 4.2 第二期：方案 B（自启 agent，对齐 MFAAvalonia）

参考 [AgentHelper.cs:75-391](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L75-L391)：

- [ ] **新文件** [maa_mcp/agent_runtime.py](maa_mcp/agent_runtime.py)：与 maa_mcp 同进程，import 用户的 agent 模块（从 `resource_path` 猜或参数传入），调 `AgentServer.start_up(identifier)` 阻塞 join
- [ ] **配置支持**：识别项目里 `interface.json` 的 `agent` 字段（参考 [MaaInterface.cs:954-966](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/MaaInterface.cs#L954-L966)），或新增 MCP 工具 `start_agent(child_exec, child_args, identifier?, timeout?)`
- [ ] **新文件** [maa_mcp/agent_supervisor.py](maa_mcp/agent_supervisor.py)：负责 subprocess 生命周期
  - `subprocess.Popen`（**TCP 模式**用 `create_tcp(0)`，identifier 直接是 `"127.0.0.1:<port>"`）
  - **Windows JobObject 绑定**（参考 [AgentHelper.cs:731-786](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L731-L786)）
  - stdout/stderr 流式读取转发到 logger
  - **3 次 LinkStart 重试**（[AgentHelper.cs:239-317](F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs#L239-L317)）
  - `atexit` → 先 `LinkStop` client 再 kill 进程
- [ ] **整合到 `run_pipeline`**：在 `tasker = get_or_create_tasker(...)` 之后插入 `agent_supervisor.start_all(agent_configs)`；任务结束反向拆

## 5. 关键 API 速查

### 5.1 Python 绑定

| 用途 | 调用 |
|---|---|
| 创建 client | `AgentClient(identifier=None)` 或 `AgentClient.create_tcp(port=0)` |
| 绑 resource | `client.bind(resource)` → `MaaAgentClientBindResource` |
| 转发事件 | `client.register_sink(resource, controller, tasker)` |
| 等 Server | `client.connect()`（要先 `set_timeout(ms)`） |
| 拿 id | `client.identifier`（TCP 模式返回 `"127.0.0.1:<port>"`） |
| 状态 | `client.connected` / `client.alive` |
| 断开 | `client.disconnect()` |
| Server 端 | `AgentServer.start_up(identifier)` / `join()` / `shut_down()` |

### 5.2 C++ 一阶 API（参考，但 Python 绑定已经包好）

```c
MaaAgentClient* MaaAgentClientCreateV2(MaaStringBuffer* identifier);  // AF_UNIX
MaaAgentClient* MaaAgentClientCreateTcp(uint16_t port);                // TCP，port=0 自动分配
MaaBool         MaaAgentClientIdentifier(MaaAgentClient*, MaaStringBuffer* out);
MaaBool         MaaAgentClientBindResource(MaaAgentClient*, MaaResource*);
MaaBool         MaaAgentClientRegisterResourceSink(MaaAgentClient*, MaaResource*);
MaaBool         MaaAgentClientRegisterControllerSink(MaaAgentClient*, MaaController*);
MaaBool         MaaAgentClientRegisterTaskerSink(MaaAgentClient*, MaaTasker*);
MaaBool         MaaAgentClientConnect(MaaAgentClient*);
MaaBool         MaaAgentClientDisconnect(MaaAgentClient*);
MaaBool         MaaAgentClientConnected(MaaAgentClient*);
MaaBool         MaaAgentClientAlive(MaaAgentClient*);
MaaBool         MaaAgentClientSetTimeout(MaaAgentClient*, int64_t ms);
```

## 6. 风险点 & 待确认

1. **AF_UNIX 在 Windows Build < 17063 不支持**：[agent_client.py:23-32](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/agent_client.py#L23-L32) 自动 fallback TCP。**结论**：MaaMCP 干脆默认走 `create_tcp(0)`，identifier 用 `"127.0.0.1:<port>"`，跨平台行为一致。Server 端照样接这个 identifier。
2. **MaaMCP 进程不能同时是 Server 和 Client**（[agent_client.py:256-257](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/agent_client.py#L256-L257)）：方案 B 的子进程必须 fork 出去；不能 in-process。
3. **进程隔离 & 资源安全**：MAAGC 的 CustomAction 内部会 `context.tasker.controller.post_click(...)`、发请求、回 task，全部走 IPC。性能可接受但延迟要监控（`set_timeout(5000)` 起）。
4. **多个 controller / 多个 pipeline 并发**：每个 controller_id 复用一个 `Tasker`（已有逻辑），但 `Resource` 是全局共享的，**AgentClient 应当按 (resource_handle_id, identifier) 缓存**而不是单例——否则会跟 resource hash/状态打架。
5. **GC 防护**：[resource.py:55-56](C:/Users/29046/AppData/Local/Programs/Python/Python310/lib/site-packages/maa/resource.py#L55-L56) 用 `_custom_action_holder` 防 GC。我们也要建一个 `_agent_client_holder` 持有 client，否则会被 ctypes 回收。
6. **agent 子进程崩溃 / 重启**（第二期才需要管）：MaaMCP 这边 `client.connected` 会变 false，需要支持 reconnect。MFAAvalonia 没做自动 reconnect，只能手动重启整个 GUI 任务。**MaaMCP 至少要明确报错**，不能静默失败。
7. **Windows JobObject 绑定**（第二期）：MaaMCP 是 Python，用 `pywin32` 或 `ctypes` + `kernel32.dll` 自己 P/Invoke。**优先级 P1**，否则 GUI 崩溃时容易留孤儿 agent。

## 7. 结论

- **核心结论**：MaaMCP 跑不了 Custom 节点是因为没有创建 `AgentClient` 并 `bind(resource)`，框架查表查不到回调名称。补上这一段就能跑 MAAGC 的 `Auto_FightTask`。
- **推荐路径**：
  - **第一期**：方案 A（最小改动，复用 MAAGC 已有的 agent 进程），预估 < 200 行代码 + 一份单测
  - **第二期**：方案 B（对齐 MFAAvalonia，自启 agent 子进程 + 全套生命周期管理），预估 ~500 行
- **下一步**：先和用户确认 socket_id 约定（沿用 `default_socket_id` 还是每次新生成？是否需要 MCP 工具自动拉起 MAAGC agent？），再决定 3.1 vs 3.2。

## Why
让 MaaMCP 从"纯 OCR + Click 串行自动化"升级为"既能串行又能跑含 Custom 业务逻辑的完整 Pipeline"，打通 MAAGC 这种项目对 MCP 的最后一公里。

## How to apply
任何涉及 [[maa-mcp-run-pipeline]] 增强、或想用 MCP 跑 MAAGC/类似项目 pipeline 的工作，都从本文件的"3. 推荐方案设计"和"4. 落地清单"开始。Core API 引用参考第 5 节。
