import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "project_pipeline_init"
    / "scripts"
    / "analyze_pipeline_project.py"
)


def load_analyzer():
    spec = importlib.util.spec_from_file_location("analyze_pipeline_project", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_consumer_project(tmp_path: Path) -> Path:
    root = tmp_path / "MaaExampleGame"
    assets = root / "assets"
    write_json(
        assets / "interface.json",
        {
            "name": "MaaExampleGame",
            "url": "https://example.invalid/MaaExampleGame",
            "controller": [{"name": "ADB 默认方式", "type": "Adb"}],
            "resource": [
                {"name": "官服", "path": ["./resource/base"]},
                {"name": "渠道服", "path": ["./resource/base", "./resource/channel"]},
            ],
            "agent": {
                "child_exec": "python",
                "child_args": ["-u", "./agent/main.py"],
            },
            "task": [
                {"name": "启动游戏", "entry": "Start"},
                {"name": "每日任务", "entry": "DailyTask"},
            ],
        },
    )
    write_json(
        assets / "resource" / "base" / "default_pipeline.json",
        {
            "Default": {"post_delay": 100},
            "TemplateMatch": {"recognition": "TemplateMatch", "threshold": 0.7},
        },
    )
    write_json(
        assets / "resource" / "base" / "pipeline" / "utils.json",
        {
            "BackText": {
                "recognition": "OCR",
                "expected": "返回",
                "roi": [500, 1100, 180, 80],
                "action": "Click",
            },
            "ConfirmButton": {
                "recognition": "OCR",
                "expected": ["确定", "确认"],
                "roi": [30, 400, 660, 420],
                "action": "Click",
            },
            "PopupClose": {
                "recognition": "TemplateMatch",
                "template": "utils/Close.png",
                "action": "Click",
            },
            "AndroidBackKey": {
                "recognition": "DirectHit",
                "action": "ClickKey",
                "key": 4,
            },
            "ReturnHall": {
                "recognition": "DirectHit",
                "next": [
                    "CheckHall",
                    "[JumpBack]BackText",
                    {"name": "ConfirmButton", "jump_back": True},
                ],
            },
            "CheckHall": {
                "recognition": "OCR",
                "expected": "大厅",
            },
        },
    )
    write_json(
        assets / "resource" / "base" / "pipeline" / "main.json",
        {
            "Start": {
                "next": ["TaskNode", "[JumpBack]ReturnHall"],
                "on_error": "ConfirmButton",
                "interrupt": ["PopupClose"],
            },
            "TaskNode": {
                "recognition": "TemplateMatch",
                "template": "task/Task.png",
                "action": "Click",
                "next": ["AndroidBackKey", "MissingNode"],
            },
            "DailyTask": {
                "recognition": "DirectHit",
                "next": ["TaskNode", "[JumpBack]BackText"],
            },
            "SelfLoop": {
                "recognition": "DirectHit",
                "next": "SelfLoop",
            },
            "IsolatedProbe": {
                "recognition": "OCR",
                "expected": "孤立",
            },
        },
    )
    write_json(
        assets / "resource" / "channel" / "pipeline" / "start_up.json",
        {
            "ChannelStart": {
                "recognition": "DirectHit",
                "next": ["Start"],
            }
        },
    )
    for image in [
        assets / "resource" / "base" / "image" / "utils" / "Close.png",
        assets / "resource" / "base" / "image" / "utils" / "BackButton.png",
        assets / "resource" / "base" / "image" / "task" / "Task.png",
    ]:
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"png")
    return root


def test_analyze_project_finds_entries_edges_common_nodes_and_images(tmp_path: Path):
    analyzer = load_analyzer()
    root = make_consumer_project(tmp_path)

    result = analyzer.analyze_project(root)
    pipeline = result["pipeline"]

    assert result["project_name"] == "MaaExampleGame"
    assert result["controllers"] == ["Adb"]
    assert {task["entry"] for task in result["tasks"]} == {"Start", "DailyTask"}
    assert result["pipeline_file_count"] == 3
    assert pipeline["edge_type_counts"]["next"] >= 8
    assert pipeline["edge_type_counts"]["on_error"] == 1
    assert pipeline["edge_type_counts"]["interrupt"] == 1
    assert "MissingNode" in pipeline["unresolved_refs"]
    assert ["SelfLoop"] in pipeline["cycle_candidates"]

    common_names = {item["name"] for item in pipeline["common_nodes"]}
    return_names = {item["name"] for item in pipeline["return_exit_nodes"]}
    confirm_names = {item["name"] for item in pipeline["confirm_nodes"]}

    assert {"BackText", "ConfirmButton", "ReturnHall"} <= common_names
    assert {"BackText", "ReturnHall", "AndroidBackKey"} <= return_names
    assert "ConfirmButton" in confirm_names
    assert result["image_summary"]["image_count"] == 3
    assert any(item["dir"].endswith("utils") for item in result["image_summary"]["top_dirs"])


def test_render_and_write_basic_info_refuses_existing_file(tmp_path: Path):
    analyzer = load_analyzer()
    root = make_consumer_project(tmp_path)
    result = analyzer.analyze_project(root)

    content = analyzer.render_basic_info(result)
    assert "MaaExampleGame" in content
    assert "Start" in content
    assert "BackText" in content
    assert "ConfirmButton" in content
    assert "MissingNode" in content
    assert "TemplateMatch" in content

    written = analyzer.write_basic_info(result)
    assert written == root / "basic_info.md"
    assert written.read_text(encoding="utf-8") == content

    with pytest.raises(FileExistsError):
        analyzer.write_basic_info(result)

    overwritten = analyzer.write_basic_info(result, overwrite=True)
    assert overwritten == written
