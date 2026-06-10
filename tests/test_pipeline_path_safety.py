"""Pipeline 路径沙箱安全测试 (issue #31)。

覆盖：
- ensure_within_pipelines_dir 纯函数行为（合法/越界/..  /空/None/符号链接）
- save_pipeline 的 output_path 沙箱
- load_pipeline 的路径沙箱
- _read_and_validate_pipelines 不沙箱（保留 run_pipeline 读取项目目录的能力）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_mcp.paths import ensure_within_pipelines_dir
from maa_mcp.pipeline_tools import (
    _read_and_validate_pipelines,
    load_pipeline,
    save_pipeline,
)

# fastmcp 2.x wraps @mcp.tool functions in FunctionTool; 3.x does not.
load_pipeline = getattr(load_pipeline, "fn", load_pipeline)
save_pipeline = getattr(save_pipeline, "fn", save_pipeline)


@pytest.mark.unit
class TestEnsureWithinPipelinesDir:
    """ensure_within_pipelines_dir 纯函数测试。"""

    @pytest.fixture
    def fake_data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """把 get_data_dir 重定向到 tmp_path 下，并确保 pipelines/ 子目录存在。"""
        fake = tmp_path / "data"
        fake.mkdir()
        (fake / "pipelines").mkdir()
        monkeypatch.setattr("maa_mcp.paths.get_data_dir", lambda: fake)
        return fake

    def test_relative_path_inside_dir_resolves(self, fake_data_dir: Path):
        out = ensure_within_pipelines_dir("my_pipeline.json")
        assert out == (fake_data_dir / "pipelines" / "my_pipeline.json").resolve()

    def test_absolute_path_inside_dir_allowed(self, fake_data_dir: Path):
        candidate = fake_data_dir / "pipelines" / "sub" / "x.json"
        candidate.parent.mkdir(parents=True)
        out = ensure_within_pipelines_dir(candidate)
        assert out == candidate.resolve()

    def test_absolute_path_outside_dir_rejected(self, fake_data_dir: Path):
        with pytest.raises(ValueError, match="路径越界"):
            ensure_within_pipelines_dir("/tmp/owned.json")

    def test_parent_traversal_rejected(self, fake_data_dir: Path):
        with pytest.raises(ValueError, match="路径越界"):
            ensure_within_pipelines_dir("../../etc/owned.json")

    def test_empty_string_rejected(self, fake_data_dir: Path):
        with pytest.raises(ValueError, match="路径不能为空"):
            ensure_within_pipelines_dir("")

    def test_none_rejected(self, fake_data_dir: Path):
        with pytest.raises(TypeError):
            ensure_within_pipelines_dir(None)  # type: ignore[arg-type]

    def test_symlink_outside_dir_rejected(self, fake_data_dir: Path, tmp_path: Path):
        """pipelines/ 内的符号链接若指向外部，应在 resolve() 后被拒。"""
        target = tmp_path / "owned"
        target.write_text("attacker", encoding="utf-8")
        link = fake_data_dir / "pipelines" / "evil"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this platform")
        with pytest.raises(ValueError, match="路径越界"):
            ensure_within_pipelines_dir(link)


@pytest.mark.unit
class TestSavePipelineSandbox:
    """save_pipeline 沙箱行为测试。"""

    PIPELINE_JSON = json.dumps({"NodeA": {"recognition": "DirectHit"}})

    @pytest.fixture
    def fake_data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake = tmp_path / "data"
        (fake / "pipelines").mkdir(parents=True)
        # 必须同时 patch 两个命名空间：paths.get_data_dir (被沙箱 helper 用)
        # 和 pipeline_tools.get_data_dir (被 save_pipeline 的默认分支用)
        monkeypatch.setattr("maa_mcp.paths.get_data_dir", lambda: fake)
        monkeypatch.setattr("maa_mcp.pipeline_tools.get_data_dir", lambda: fake)
        return fake

    def test_default_branch_still_writes_to_pipelines_dir(self, fake_data_dir: Path):
        out = save_pipeline(self.PIPELINE_JSON)
        assert str(out).startswith(str(fake_data_dir / "pipelines"))
        assert Path(out).exists()

    def test_valid_relative_path_inside_pipelines(self, fake_data_dir: Path):
        out = save_pipeline(self.PIPELINE_JSON, output_path="custom.json")
        assert out.endswith("custom.json")
        assert Path(out).exists()

    def test_traversal_rejected(self, fake_data_dir: Path, tmp_path: Path):
        """PoC from issue #31: ../../etc/owned.json 应被拒绝。"""
        result = save_pipeline(self.PIPELINE_JSON, output_path="../../etc/owned.json")
        assert isinstance(result, str)
        assert "路径越界" in result or "参数错误" in result
        assert not (tmp_path / "etc" / "owned.json").exists()

    def test_absolute_outside_path_rejected(self, fake_data_dir: Path, tmp_path: Path):
        target = tmp_path / "owned.json"
        result = save_pipeline(self.PIPELINE_JSON, output_path=str(target))
        assert isinstance(result, str)
        assert "路径越界" in result or "参数错误" in result
        assert not target.exists()


@pytest.mark.unit
class TestLoadPipelineSandbox:
    """load_pipeline 沙箱行为测试。"""

    @pytest.fixture
    def fake_data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake = tmp_path / "data"
        (fake / "pipelines").mkdir(parents=True)
        monkeypatch.setattr("maa_mcp.paths.get_data_dir", lambda: fake)
        return fake

    def _write(self, path: Path, data: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_within_pipelines_succeeds(self, fake_data_dir: Path):
        p = self._write(fake_data_dir / "pipelines" / "ok.json", {"N": {"x": 1}})
        out = load_pipeline(str(p))
        assert out == {"N": {"x": 1}}

    def test_load_external_file_rejected(self, fake_data_dir: Path, tmp_path: Path):
        """Issue #31 PoC: 加载 /tmp/owned.json 越界路径。"""
        p = self._write(tmp_path / "owned.json", {"N": {"x": 1}})
        out = load_pipeline(str(p))
        assert isinstance(out, str)
        assert "路径越界" in out or "参数错误" in out

    def test_load_relative_traversal_rejected(self, fake_data_dir: Path):
        out = load_pipeline("../../etc/owned.json")
        assert isinstance(out, str)
        assert "路径越界" in out or "参数错误" in out


@pytest.mark.unit
class TestRunPipelineNotSandboxed:
    """回归测试：_read_and_validate_pipelines 仍允许任意位置，
    以保留 run_pipeline 读取项目资源目录的合法能力 (MAAGC 等)。
    """

    def test_helper_reads_external_path(self, tmp_path: Path):
        p = tmp_path / "external.json"
        p.write_text(json.dumps({"N": {"x": 1}}), encoding="utf-8")
        # _read_and_validate_pipelines 仍允许任意位置 (intentional)
        out = _read_and_validate_pipelines([str(p)])
        assert out[0][1] == {"N": {"x": 1}}
