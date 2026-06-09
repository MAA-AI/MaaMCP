"""
Agent 子进程生命周期管理（MFAAvalonia 对齐版）

负责：
- 从项目的 interface.json 读 agent 启动配置
- 启动 agent Python 子进程并 bind 到当前 Resource / Controller / Tasker
- 通过 TCP 模式 + 随机 8 位 identifier 跨平台通信
- Windows JobObject 绑定（父进程死时自动杀子进程，杜绝孤儿进程）
- 提供 start / stop / shutdown_all API 给 MCP 工具调用
- 子进程 stdout/stderr 流式转发到 loguru

参考实现：F:/workspace/MFAAvalonia/MFAAvalonia/Extensions/MaaFW/AgentHelper.cs
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import random
import re
import string
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger
from maa.agent_client import AgentClient
from maa.controller import Controller
from maa.resource import Resource
from maa.tasker import Tasker

from maa_mcp.core import object_registry

# ---------------------------------------------------------------------------
# 平台相关常量（Windows JobObject）
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _CREATE_NO_WINDOW = 0x08000000
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentConfig:
    """从 interface.json 的 'agent' 块解析出的启动配置。"""

    child_exec: str
    child_args: List[str]
    project_root: Path
    identifier: Optional[str] = None
    timeout: int = 30
    auto_start: bool = True


@dataclass
class AgentContext:
    """一个 controller_id 对应一个 agent 运行时。"""

    controller_id: str
    config: AgentConfig
    identifier: str
    client: AgentClient
    process: subprocess.Popen
    started_at: float = 0.0
    stopped: bool = False


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

# controller_id -> AgentContext
_agents: Dict[str, AgentContext] = {}
_lock = threading.Lock()

# ANSI 转义序列剥离（agent 日志常带颜色码）
_ANSI_RE = re.compile(r"\x1B\[[0-9;]*[a-zA-Z]")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_agent_config(resource_path: Optional[str]) -> Optional[AgentConfig]:
    """从 interface.json 读 agent 启动配置。

    向上递归查找（最多 4 层）：
    1. <resource_path>/interface.json
    2. <resource_path>/../interface.json
    3. <resource_path>/../../interface.json
    4. <resource_path>/../../../interface.json

    兼容 MAAGC 的目录结构：
      F:/workspace/MAAGC/assets/interface.json   ← interface.json 在此
      F:/workspace/MAAGC/assets/resource/base/   ← resource_path 通常指向这里

    找不到、没 agent 块、解析失败 → 返回 None（run_pipeline 会跳过，不报错）。
    """
    if not resource_path:
        return None
    try:
        resource = Path(resource_path).resolve()
    except OSError:
        return None

    # 沿 ancestor chain 向上查找（resource 自身 + 4 层 parent）
    candidates: List[Path] = [resource, *resource.parents][:5]
    for ancestor in candidates:
        cfg_path = ancestor / "interface.json"
        if not cfg_path.is_file():
            continue
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"解析 {cfg_path} 失败: {e}")
            return None
        if not isinstance(data, dict):
            return None
        agent_block = data.get("agent")
        if not isinstance(agent_block, dict) or not agent_block.get("child_exec"):
            return None
        try:
            return AgentConfig(
                child_exec=str(agent_block["child_exec"]),
                child_args=[str(a) for a in agent_block.get("child_args", [])],
                project_root=cfg_path.parent,
                identifier=(
                    str(agent_block["identifier"])
                    if agent_block.get("identifier")
                    else None
                ),
                timeout=int(agent_block.get("timeout", 30)),
                auto_start=bool(agent_block.get("auto_start", True)),
            )
        except (TypeError, ValueError) as e:
            logger.warning(f"interface.json agent 字段类型错误 ({cfg_path}): {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _gen_identifier() -> str:
    """生成 8 位 alphanumeric identifier（参考 MFAAvalonia AgentHelper.cs:133-136）。"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=8))


def _build_subprocess_cmd(cfg: AgentConfig, identifier: str) -> List[str]:
    """拼接最终命令行：child_exec + child_args（替换 .py 相对路径）+ [identifier]。

    identifier 作为最后一个位置参数传入；MAAGC agent/main.py:382 用 sys.argv[-1] 读它。
    """
    root = cfg.project_root
    resolved_args: List[str] = []
    for arg in cfg.child_args:
        if arg.lower().endswith(".py") and not Path(arg).is_absolute():
            try:
                resolved_args.append(str((root / arg).resolve()))
            except OSError:
                resolved_args.append(arg)
        else:
            resolved_args.append(arg)
    return [cfg.child_exec, *resolved_args, identifier]


def _start_stream_loggers(proc: subprocess.Popen, controller_id: str) -> None:
    """两个 daemon 线程读 stdout/stderr，转发到 loguru。"""

    def _pump(stream, level: str) -> None:
        for raw in iter(stream.readline, b""):
            try:
                line = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                continue
            line = _ANSI_RE.sub("", line)
            if line:
                logger.bind(agent=controller_id).log(level, line)

    threading.Thread(
        target=_pump,
        args=(proc.stdout, "INFO"),
        daemon=True,
        name=f"agent-{controller_id}-stdout",
    ).start()
    threading.Thread(
        target=_pump,
        args=(proc.stderr, "ERROR"),
        daemon=True,
        name=f"agent-{controller_id}-stderr",
    ).start()


# ---------------------------------------------------------------------------
# Windows JobObject 绑定
# ---------------------------------------------------------------------------


def _bind_job_windows(proc: subprocess.Popen) -> None:
    """把子进程绑到 JobObject，KILL_ON_JOB_CLOSE 兜底。

    注意：
    - 必须在子进程启动后立即 AssignProcessToJobObject（subprocess.Popen 返回前
      这一窗口极小，Python 不会 fork，所以实际风险可忽略）
    - 绝对不能 CloseHandle(job) —— 句柄计数归零会立即杀子进程
    - 当 MaaMCP 进程被 taskkill /F 杀掉时，OS 自动回收 JobObject 句柄，
      KILL_ON_JOB_CLOSE 触发，子进程连带被 OS 杀掉，杜绝孤儿
    """
    if not _IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32

        # 定义 IO_COUNTERS / JOBOBJECT_BASIC_LIMIT_INFORMATION / JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSize", ctypes.c_size_t),
                ("MaximumWorkingSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.warning("CreateJobObjectW 失败，Job 兜底不可用")
            kernel32.ResumeThread(int(proc._handle))
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.warning("SetInformationJobObject 失败")
            kernel32.CloseHandle(job)
            return

        if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
            logger.warning("AssignProcessToJobObject 失败")
            kernel32.CloseHandle(job)
            return

        # 故意不 CloseHandle(job) —— 句柄计数 > 0，JobObject 持续生效
        # 进程退出时由 OS 回收句柄，KILL_ON_JOB_CLOSE 触发
        logger.debug("JobObject 已绑定")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"JobObject 绑定异常: {e}（子进程不会因本异常崩溃，仅失去 OS 兜底）"
        )


# ---------------------------------------------------------------------------
# 子进程启动
# ---------------------------------------------------------------------------


def _spawn_agent(cfg: AgentConfig, identifier: str) -> subprocess.Popen:
    cmd = _build_subprocess_cmd(cfg, identifier)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["MAA_AGENT_IDENTIFIER"] = identifier

    logger.info(f"启动 agent: cmd={cmd} cwd={cfg.project_root}")

    if _IS_WINDOWS:
        # 注意：故意不用 CREATE_SUSPENDED。Python 不 fork，AssignProcessToJobObject
        # 在 Popen 返回后立刻调，子进程还没机会跑出可观察的副作用；
        # 而 CREATE_SUSPENDED 需要拿线程句柄（proc._handle 是进程句柄）才能
        # ResumeThread，stdin/stdout/stderr 是 PIPE 时取不到主线程句柄，
        # 会导致子进程永远卡在挂起状态、AgentServer 永远不启、connect 永远 timeout。
        proc = subprocess.Popen(
            cmd,
            cwd=str(cfg.project_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
        )
        _bind_job_windows(proc)
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cfg.project_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # 独立 process group，便于 os.killpg
        )
    return proc


# ---------------------------------------------------------------------------
# 公开 API：start / stop / shutdown_all
# ---------------------------------------------------------------------------


def start(
    resource_path: Optional[str],
    controller_id: str,
    resource: Resource,
    controller: Controller,
    tasker: Tasker,
) -> Optional[AgentContext]:
    """启动 agent 子进程并 bind 到 resource / controller / tasker。

    若该 controller_id 已有运行中的 agent，复用之。返回 None 表示无 agent 配置
    （纯 JSON pipeline，无需 agent），调用方应直接继续跑 task。
    """
    cfg = load_agent_config(resource_path)
    if cfg is None or not cfg.auto_start:
        return None

    with _lock:
        existing = _agents.get(controller_id)
        if existing is not None and not existing.stopped:
            return existing

        # 选择 client 构造方式：
        #   - interface.json 指定了 identifier → 用指定 id（AF_UNIX / 已知名）
        #   - 否则 → TCP 模式 + create_tcp(0) 让 OS 分配端口，identifier 是 "127.0.0.1:<port>"
        try:
            if cfg.identifier:
                client = AgentClient(cfg.identifier)
            else:
                client = AgentClient.create_tcp(0)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"AgentClient 创建失败: {e}") from e

        if not client.bind(resource):
            raise RuntimeError(f"AgentClient.bind(resource) 失败: {controller_id}")
        if not client.register_sink(resource, controller, tasker):
            raise RuntimeError(f"AgentClient.register_sink 失败: {controller_id}")
        client.set_timeout(cfg.timeout * 1000)

        try:
            proc = _spawn_agent(cfg, client.identifier)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"启动 agent 子进程失败: {e}") from e

        _start_stream_loggers(proc, controller_id)

        # 3 次重试 connect（参考 AgentHelper.cs:239-317）
        link_ok = False
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                link_ok = client.connect()
                if link_ok:
                    break
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(f"Agent connect 第 {attempt}/3 次失败: {e}")
            if proc.poll() is not None:
                logger.error(
                    f"Agent 子进程在 connect 之前已退出 (exit={proc.returncode})"
                )
                break
            if attempt < 3:
                time.sleep(attempt)

        if not link_ok:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            detail = f"last_err={last_err}" if last_err else "connect() 返回 False"
            raise RuntimeError(
                f"Agent 启动失败 (identifier={client.identifier}): {detail}"
            )

        ctx = AgentContext(
            controller_id=controller_id,
            config=cfg,
            identifier=client.identifier,
            client=client,
            process=proc,
            started_at=time.time(),
        )
        _agents[controller_id] = ctx
        logger.info(
            f"Agent 启动成功: controller_id={controller_id} "
            f"identifier={client.identifier} pid={proc.pid}"
        )
        return ctx


def stop(controller_id: str, grace_seconds: float = 3.0) -> dict:
    """原子地停 Tasker / Agent client / 子进程 / JobObject（best-effort）。

    返回每步执行状态 dict，让 AI 能据此判断哪些步骤失败。
    """
    result: dict = {
        "controller_id": controller_id,
        "tasker_stopped": False,
        "client_disconnected": False,
        "process_killed": False,
        "registry_cleaned": False,
    }

    # 1) Tasker
    tasker_key = f"_tasker_{controller_id}"
    tasker = object_registry.get(tasker_key)
    if tasker is not None:
        try:
            tasker.stop()
            result["tasker_stopped"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tasker.stop 失败: {e}")

    # 2) Agent client disconnect + 子进程 terminate
    with _lock:
        ctx = _agents.get(controller_id)

    if ctx is not None and not ctx.stopped:
        try:
            ctx.client.disconnect()
            result["client_disconnected"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"client.disconnect 失败: {e}")

        try:
            ctx.process.terminate()
            try:
                ctx.process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"子进程未在 {grace_seconds}s 内退出，发送 SIGKILL/TerminateProcess"
                )
                try:
                    ctx.process.kill()
                    ctx.process.wait(timeout=2)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"proc.kill 失败: {e}")
            result["process_killed"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"子进程终止失败: {e}")

        # 4) Windows JobObject: 故意不显式 close —— OS 在 MaaMCP 进程退出时回收
        ctx.stopped = True

    # 5) Registry 清理（无论 ctx 是否存在都执行）
    object_registry.unregister(tasker_key)
    object_registry.unregister(controller_id)
    result["registry_cleaned"] = True

    with _lock:
        _agents.pop(controller_id, None)
    return result


def shutdown_all() -> None:
    """atexit 兜底：关掉所有还在跑的 agent 子进程。

    注意：atexit 在 SIGKILL 时不触发；Windows 上靠 JobObject 兜底。
    """
    with _lock:
        ids = list(_agents.keys())
    if not ids:
        return
    logger.info(f"atexit shutdown_all: 关闭 {len(ids)} 个 agent")
    for cid in ids:
        try:
            stop(cid)
        except Exception as e:  # noqa: BLE001
            logger.error(f"shutdown_all stop({cid}) 失败: {e}")


# 注册到 atexit，与 maa_mcp/core.py 的 cleanup_screenshots 并存
# LIFO 顺序：agent 清理先于 screenshot 清理，避免日志/截图依赖
atexit.register(shutdown_all)
