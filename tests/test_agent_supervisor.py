"""Agent 子进程生命周期管理（agent_supervisor）相关测试。

覆盖：
- 纯函数单元测试：identifier 生成、命令行拼装、interface.json 解析
- 集成测试：start / stop 循环、复用、异常处理
- 平台相关：Windows JobObject 路径（仅在 win32 跑）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maa_mcp.agent_supervisor import (
    AgentConfig,
    AgentContext,
    _agents,
    _build_subprocess_cmd,
    _gen_identifier,
    _lock,
    load_agent_config,
    shutdown_all,
    start,
    stop,
)

# ---------------------------------------------------------------------------
# 单元测试（不依赖 maafw 运行时 / 不真启子进程）
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenIdentifier:
    def test_length_eight(self):
        for _ in range(50):
            assert len(_gen_identifier()) == 8

    def test_alphanumeric_only(self):
        for _ in range(50):
            ident = _gen_identifier()
            assert re.fullmatch(r"[A-Za-z0-9]{8}", ident), ident

    def test_unique_with_high_probability(self):
        # 1000 个不重复的概率 ≈ 1 - C(1000,2)/62^8 ≈ 1 - 4.5e-13
        ids = {_gen_identifier() for _ in range(1000)}
        assert len(ids) == 1000


@pytest.mark.unit
class TestBuildSubprocessCmd:
    def _cfg(self, args, root: Path) -> AgentConfig:
        return AgentConfig(
            child_exec="python",
            child_args=list(args),
            project_root=root,
        )

    def test_relative_py_resolved_against_project_root(self, tmp_path: Path):
        # 创建 fake 脚本以让 resolve 成功
        script = tmp_path / "agent" / "main.py"
        script.parent.mkdir(parents=True)
        script.touch()
        cfg = self._cfg(["-u", "agent/main.py"], tmp_path)
        cmd = _build_subprocess_cmd(cfg, "abc12345")
        assert cmd[0] == "python"
        assert cmd[1] == "-u"
        assert cmd[2] == str(script.resolve())
        assert cmd[-1] == "abc12345"  # identifier 在末位

    def test_absolute_py_kept_as_is(self, tmp_path: Path):
        script = tmp_path / "main.py"
        script.touch()
        cfg = self._cfg([str(script)], tmp_path)
        cmd = _build_subprocess_cmd(cfg, "id1")
        assert cmd[1] == str(script)
        assert cmd[-1] == "id1"

    def test_non_py_args_kept_as_is(self, tmp_path: Path):
        cfg = self._cfg(["-u", "-X", "utf8"], tmp_path)
        cmd = _build_subprocess_cmd(cfg, "id1")
        assert cmd == ["python", "-u", "-X", "utf8", "id1"]

    def test_identifier_appended_last(self, tmp_path: Path):
        cfg = self._cfg(["agent/main.py"], tmp_path)
        cfg.project_root.mkdir(exist_ok=True)
        (tmp_path / "agent").mkdir(exist_ok=True)
        (tmp_path / "agent" / "main.py").touch()
        cmd = _build_subprocess_cmd(cfg, "ZZZZZZZZ")
        assert cmd[-1] == "ZZZZZZZZ"


@pytest.mark.unit
class TestLoadAgentConfig:
    def test_returns_none_when_no_resource_path(self):
        assert load_agent_config(None) is None

    def test_returns_none_when_no_interface_json(self, tmp_path: Path):
        assert load_agent_config(str(tmp_path)) is None

    def test_parses_full_block(self, tmp_path: Path):
        cfg_path = tmp_path / "interface.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "name": "test",
                    "agent": {
                        "child_exec": "python.exe",
                        "child_args": ["-u", "agent/main.py"],
                        "identifier": "my_id",
                        "timeout": 60,
                        "auto_start": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        cfg = load_agent_config(str(tmp_path))
        assert cfg is not None
        assert cfg.child_exec == "python.exe"
        assert cfg.child_args == ["-u", "agent/main.py"]
        assert cfg.identifier == "my_id"
        assert cfg.timeout == 60
        assert cfg.auto_start is True
        assert cfg.project_root == tmp_path

    def test_finds_interface_json_in_parent(self, tmp_path: Path):
        # resource_path 指向 tmp_path/resource/base，interface.json 在 tmp_path/interface.json
        resource = tmp_path / "resource" / "base"
        resource.mkdir(parents=True)
        cfg_path = tmp_path / "interface.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "agent": {
                        "child_exec": "python",
                        "child_args": ["agent/main.py"],
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_agent_config(str(resource))
        assert cfg is not None
        assert cfg.project_root == tmp_path
        assert cfg.timeout == 30  # 默认值
        assert cfg.auto_start is True  # 默认值

    def test_returns_none_when_no_agent_block(self, tmp_path: Path):
        (tmp_path / "interface.json").write_text(
            json.dumps({"name": "x"}), encoding="utf-8"
        )
        assert load_agent_config(str(tmp_path)) is None

    def test_returns_none_when_agent_block_no_child_exec(self, tmp_path: Path):
        (tmp_path / "interface.json").write_text(
            json.dumps({"agent": {"child_args": []}}), encoding="utf-8"
        )
        assert load_agent_config(str(tmp_path)) is None

    def test_returns_none_on_invalid_json(self, tmp_path: Path):
        (tmp_path / "interface.json").write_text("not json", encoding="utf-8")
        assert load_agent_config(str(tmp_path)) is None

    def test_returns_none_on_invalid_timeout_type(self, tmp_path: Path):
        (tmp_path / "interface.json").write_text(
            json.dumps({"agent": {"child_exec": "python", "timeout": "abc"}}),
            encoding="utf-8",
        )
        assert load_agent_config(str(tmp_path)) is None


@pytest.mark.unit
class TestStopOrder:
    """验证 stop() 内部调用顺序：tasker → client → proc → registry。"""

    def setup_method(self):
        # 清空全局状态防止跨测试干扰
        with _lock:
            _agents.clear()

    def teardown_method(self):
        with _lock:
            _agents.clear()

    def test_stop_calls_tasker_first(self):
        from maa_mcp.core import object_registry

        controller_id = "ctrl_test_order_1"
        tasker = MagicMock()
        client = MagicMock()
        proc = MagicMock(spec=subprocess.Popen)
        proc.wait.return_value = 0  # terminate 立即完成
        ctx = AgentContext(
            controller_id=controller_id,
            config=MagicMock(spec=AgentConfig),
            identifier="id123",
            client=client,
            process=proc,
        )
        with _lock:
            _agents[controller_id] = ctx
        object_registry.register_by_name(f"_tasker_{controller_id}", tasker)

        call_order: list = []
        tasker.stop.side_effect = lambda: call_order.append("tasker")
        client.disconnect.side_effect = lambda: call_order.append("client")
        proc.terminate.side_effect = lambda: call_order.append("proc")

        result = stop(controller_id, grace_seconds=0.1)

        assert call_order == ["tasker", "client", "proc"]
        assert result["tasker_stopped"] is True
        assert result["client_disconnected"] is True
        assert result["process_killed"] is True
        assert result["registry_cleaned"] is True
        # 清理
        object_registry.unregister(controller_id)
        object_registry.unregister(f"_tasker_{controller_id}")

    def test_stop_works_when_no_agent_running(self):
        from maa_mcp.core import object_registry

        controller_id = "ctrl_test_no_agent"
        # 注册一个 fake controller
        object_registry.register_by_name(controller_id, MagicMock())
        object_registry.register_by_name(f"_tasker_{controller_id}", MagicMock())

        result = stop(controller_id, grace_seconds=0.1)

        # tasker.stop 会调，但 client/proc 步骤跳过
        assert result["tasker_stopped"] is True
        assert result["client_disconnected"] is False
        assert result["process_killed"] is False
        assert result["registry_cleaned"] is True

    def test_stop_does_not_raise_when_proc_already_dead(self):
        from maa_mcp.core import object_registry

        controller_id = "ctrl_test_dead_proc"
        client = MagicMock()
        proc = MagicMock(spec=subprocess.Popen)
        # proc.terminate 抛 ProcessLookupError（已死）
        proc.terminate.side_effect = ProcessLookupError("already dead")
        ctx = AgentContext(
            controller_id=controller_id,
            config=MagicMock(spec=AgentConfig),
            identifier="id",
            client=client,
            process=proc,
        )
        with _lock:
            _agents[controller_id] = ctx
        object_registry.register_by_name(f"_tasker_{controller_id}", MagicMock())
        object_registry.register_by_name(controller_id, MagicMock())

        result = stop(controller_id, grace_seconds=0.1)

        # 不抛异常
        assert result["registry_cleaned"] is True

    def test_stop_force_kill_on_terminate_timeout(self):
        from maa_mcp.core import object_registry

        controller_id = "ctrl_test_force_kill"
        client = MagicMock()
        proc = MagicMock(spec=subprocess.Popen)
        # terminate 后 wait 超时
        proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 0.1), 0]
        # kill 正常返回
        ctx = AgentContext(
            controller_id=controller_id,
            config=MagicMock(spec=AgentConfig),
            identifier="id",
            client=client,
            process=proc,
        )
        with _lock:
            _agents[controller_id] = ctx
        object_registry.register_by_name(f"_tasker_{controller_id}", MagicMock())
        object_registry.register_by_name(controller_id, MagicMock())

        result = stop(controller_id, grace_seconds=0.1)
        proc.kill.assert_called_once()
        assert result["process_killed"] is True


# ---------------------------------------------------------------------------
# 集成测试：使用真实 AgentClient.create_tcp(0) + mock subprocess
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStartStopCycle:
    """需要 maafw 真实运行时 + mock subprocess.Popen。"""

    def setup_method(self):
        with _lock:
            _agents.clear()
        # 准备 fake 资源
        from maa_mcp.core import object_registry

        self._registry = object_registry
        self._registry.clear()

    def teardown_method(self):
        with _lock:
            _agents.clear()
        self._registry.clear()

    def _make_resource_controller_tasker(self):
        """构造 fake Resource / Controller / Tasker。"""
        resource = MagicMock()
        controller = MagicMock()
        tasker = MagicMock()
        return resource, controller, tasker

    def _write_interface(self, tmp_path: Path, **agent_overrides) -> Path:
        cfg_path = tmp_path / "interface.json"
        block = {"child_exec": "python", "child_args": ["agent/main.py"]}
        block.update(agent_overrides)
        cfg_path.write_text(json.dumps({"agent": block}), encoding="utf-8")
        return cfg_path

    def test_start_returns_none_when_no_interface(self, tmp_path: Path):
        resource, controller, tasker = self._make_resource_controller_tasker()
        result = start(str(tmp_path), "ctrl1", resource, controller, tasker)
        assert result is None

    def test_start_returns_none_when_auto_start_false(self, tmp_path: Path):
        self._write_interface(tmp_path, auto_start=False)
        resource, controller, tasker = self._make_resource_controller_tasker()
        result = start(str(tmp_path), "ctrl1", resource, controller, tasker)
        assert result is None

    def test_stop_with_no_context_returns_partial_result(self):
        from maa_mcp.core import object_registry

        controller_id = "ctrl_no_ctx"
        # 没有注册 controller / tasker / agent
        result = stop(controller_id, grace_seconds=0.1)
        assert result["tasker_stopped"] is False
        assert result["client_disconnected"] is False
        assert result["process_killed"] is False
        assert result["registry_cleaned"] is True

    def test_shutdown_all_clears_running_agents(self):
        # 模拟两个 agent 在跑
        for cid in ["ctrl_a", "ctrl_b"]:
            ctx = AgentContext(
                controller_id=cid,
                config=MagicMock(spec=AgentConfig),
                identifier="x",
                client=MagicMock(),
                process=MagicMock(spec=subprocess.Popen),
            )
            ctx.process.wait.return_value = 0
            with _lock:
                _agents[cid] = ctx

        shutdown_all()

        with _lock:
            assert _agents == {}


# ---------------------------------------------------------------------------
# 平台特定：Windows JobObject 绑定（仅在 win32 跑）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only")
@pytest.mark.integration
class TestWindowsJobObject:
    """Windows 平台下验证 _bind_job_windows 的关键路径（不深测 JobObject 行为）。"""

    def test_bind_job_windows_no_crash_on_valid_proc(self):
        # 用一个真实的 Popen 但立刻 kill，确保 _bind_job_windows 不会让测试卡住
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        try:
            from maa_mcp.agent_supervisor import _bind_job_windows

            _bind_job_windows(proc)
            # 至少进程句柄有效
            assert proc.pid > 0
        finally:
            proc.kill()
            proc.wait(timeout=2)


# ---------------------------------------------------------------------------
# 回归测试：spawn 出来的子进程必须真的在跑、且 sys.argv[-1] 必须是 identifier
# （防止 CREATE_SUSPENDED bug 复发）
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSpawnProcessActuallyRuns:
    """回归测试：spawn 后子进程必须真的在跑、sys.argv[-1] 必须是 identifier。"""

    def test_spawned_subprocess_runs_and_receives_identifier(self, tmp_path: Path):
        """spawn 出来的子进程必须能正常执行（不被挂起）、并能拿到 identifier。"""
        # 写一个 fake agent 脚本：把 sys.argv[-1] 写到文件，然后退出
        script = tmp_path / "fake_agent.py"
        output_file = tmp_path / "received_argv.txt"
        script.write_text(
            f"import sys; open(r'{output_file}', 'w', encoding='utf-8').write(sys.argv[-1])\n",
            encoding="utf-8",
        )
        cfg = AgentConfig(
            child_exec=sys.executable,
            child_args=[str(script)],
            project_root=tmp_path,
            timeout=10,
        )
        identifier = "test_id_12345"

        from maa_mcp.agent_supervisor import _spawn_agent

        proc = _spawn_agent(cfg, identifier)
        try:
            # 等子进程退出
            rc = proc.wait(timeout=10)
            assert rc == 0, f"子进程异常退出: rc={rc}"

            # 子进程必须真的跑过：output_file 应存在
            assert output_file.exists(), (
                "子进程未真正运行（无 CREATE_SUSPENDED 残留 bug？）"
            )
            # 且 sys.argv[-1] 必须是 identifier
            received = output_file.read_text(encoding="utf-8").strip()
            assert received == identifier, (
                f"子进程收到的 identifier 不正确: {received!r} != {identifier!r}"
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)

    def test_spawned_subprocess_cwd_is_project_root(self, tmp_path: Path):
        """验证 spawn 后的 cwd 是 project_root（让 MAAGC agent 的 chdir 正常工作）。"""
        script = tmp_path / "fake_agent.py"
        cwd_file = tmp_path / "received_cwd.txt"
        script.write_text(
            f"import os; open(r'{cwd_file}', 'w', encoding='utf-8').write(os.getcwd())\n",
            encoding="utf-8",
        )
        cfg = AgentConfig(
            child_exec=sys.executable,
            child_args=[str(script)],
            project_root=tmp_path,
        )

        from maa_mcp.agent_supervisor import _spawn_agent

        proc = _spawn_agent(cfg, "id")
        try:
            proc.wait(timeout=10)
            assert cwd_file.exists()
            received_cwd = cwd_file.read_text(encoding="utf-8").strip()
            # Windows 上 path 可能是大写或带正反斜杠
            assert Path(received_cwd).resolve() == tmp_path.resolve()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
