"""Coordinate contracts tested with native MaaFramework and synthetic frames."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.pipeline import JColorMatch, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

from maa_mcp import adb, control, vision, win32
from maa_mcp.core import controller_info_registry, mcp, object_registry
from maa_mcp.paths import get_resource_dir


class ImageController(CustomController):
    def __init__(self, image):
        self.image = image
        self.points = []
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "maamcp-coordinate-test"

    def screencap(self):
        return self.image

    def touch_down(self, contact, x, y, pressure):
        self.points.append((x, y))
        return True

    def touch_up(self, contact):
        return True


@pytest.fixture
def image_controller():
    controller = ImageController(np.zeros((1440, 2560, 3), dtype=np.uint8))
    assert controller.post_connection().wait().succeeded
    cid = object_registry.register(controller)
    try:
        yield cid, controller
    finally:
        object_registry.unregister(cid)


@pytest.fixture(params=["adb", "win32"])
def connection_probe(request, monkeypatch, image_controller):
    _, controller = image_controller
    if request.param == "win32":
        monkeypatch.setattr(
            win32, "Win32Controller", lambda *args, **kwargs: controller
        )
        source = SimpleNamespace(hwnd=123)
        connect = win32.connect_window
    else:
        monkeypatch.setattr(adb, "AdbController", lambda *args, **kwargs: controller)
        source = SimpleNamespace(
            adb_path="unused",
            address="unused",
            screencap_methods=0,
            input_methods=0,
            config="{}",
        )
        connect = adb.connect_adb_device
    source_id = object_registry.register(source)
    connections = []

    def connect_probe(**options):
        cid = getattr(connect, "fn", connect)(source_id, **options)
        if cid:
            connections.append(cid)
        return cid

    try:
        yield connect_probe, controller
    finally:
        object_registry.unregister(source_id)
        for cid in connections:
            object_registry.unregister(cid)
            controller_info_registry.pop(cid, None)


@pytest.mark.integration
@pytest.mark.parametrize(
    "options,shape",
    [
        ({}, (720, 1280, 3)),
        ({"target_short_side": 1080}, (1080, 1920, 3)),
        ({"target_short_side": None}, (1440, 2560, 3)),
    ],
)
def test_connection_screenshot_and_click_share_coordinates(
    monkeypatch, tmp_path, connection_probe, options, shape
):
    connect, controller = connection_probe
    monkeypatch.setattr(vision, "get_screenshots_dir", lambda: tmp_path)
    cid = connect(**options)
    assert cid is not None
    assert controller_info_registry[cid].connection_params[
        "target_short_side"
    ] == options.get("target_short_side", 720)
    capture = getattr(vision.screencap, "fn", vision.screencap)
    path = capture(cid)
    assert isinstance(path, str)
    image = cv2.imread(path)
    assert image.shape == shape
    click = getattr(control.click, "fn", control.click)
    assert click(cid, image.shape[1] // 2, image.shape[0] // 2, duration=0)
    assert controller.points[-1] == (1280, 720)


@pytest.mark.parametrize("target", [0, -1, False, 1.5])
def test_invalid_resolution_is_rejected(connection_probe, target):
    connect, _ = connection_probe
    with pytest.raises(ValueError, match="target_short_side"):
        connect(target_short_side=target)


@pytest.mark.parametrize("target", [720, None])
def test_failed_resolution_configuration_does_not_publish_controller(
    connection_probe, monkeypatch, target
):
    connect, controller = connection_probe
    method = (
        "set_screenshot_use_raw_size"
        if target is None
        else "set_screenshot_target_short_side"
    )
    monkeypatch.setattr(controller, method, lambda _: False)
    monkeypatch.setattr(
        controller, "post_connection", lambda: pytest.fail("Unexpected connection")
    )
    previous_ids = set(object_registry.list())
    assert connect(target_short_side=target) is None
    assert set(object_registry.list()) == previous_ids


@pytest.mark.integration
def test_scaled_clamped_crop_metadata_maps_back_to_click(
    monkeypatch, tmp_path, image_controller
):
    cid, controller = image_controller
    assert controller.set_screenshot_target_short_side(1080)
    monkeypatch.setattr(vision, "get_screenshots_dir", lambda: tmp_path)
    capture = getattr(vision.screencap, "fn", vision.screencap)
    info = capture(
        cid, region=(1500, 900, 600, 400), resolution=720, include_metadata=True
    )
    assert info["controller_id"] == cid
    assert info["coordinate_system"] == "controller"
    assert info["coordinate_size"] == (1920, 1080)
    assert info["image_size"] == (280, 120)
    assert cv2.imread(info["path"]).shape == (120, 280, 3)
    assert info["image_to_controller"] == {"scale": (1.5, 1.5), "offset": (1500, 900)}
    sx, sy = info["image_to_controller"]["scale"]
    ox, oy = info["image_to_controller"]["offset"]
    x, y = round(140 * sx + ox), round(60 * sy + oy)
    click = getattr(control.click, "fn", control.click)
    assert click(cid, x, y, duration=0)
    assert controller.points[-1] == (2280, 1320)


@pytest.mark.integration
def test_portrait_metadata_accounts_for_rounded_dimensions(
    monkeypatch, tmp_path, image_controller
):
    cid, controller = image_controller
    controller.image = np.zeros((1537, 901, 3), dtype=np.uint8)
    assert controller.set_screenshot_use_raw_size(True)
    monkeypatch.setattr(vision, "get_screenshots_dir", lambda: tmp_path)
    info = vision._screencap(cid, resolution=720, include_metadata=True)
    width, height = info["image_size"]
    sx, sy = info["image_to_controller"]["scale"]
    assert width == 720
    assert width * sx == pytest.approx(901)
    assert height * sy == pytest.approx(1537)
    assert sx != sy
    assert info["image_to_controller"]["offset"] == (0, 0)


@pytest.mark.integration
@pytest.mark.parametrize("raw_mode", [False, True])
def test_pipeline_template_requires_matching_controller_scale(
    image_controller, raw_mode
):
    _, controller = image_controller
    template = np.random.default_rng(7).integers(
        0, 256, size=(40, 60, 3), dtype=np.uint8
    )
    canonical = np.full((720, 1280, 3), 128, dtype=np.uint8)
    canonical[200:240, 400:460] = template
    controller.image = cv2.resize(
        canonical, (2560, 1440), interpolation=cv2.INTER_NEAREST
    )
    assert controller.set_screenshot_use_raw_size(raw_mode)
    resource = Resource()
    assert (
        resource.post_bundle(Path(__file__).parent / "fixtures" / "bundle_minimal")
        .wait()
        .succeeded
    )
    assert resource.override_image("coordinate-test.png", template)
    tasker = Tasker()
    tasker.bind(resource, controller)
    pipeline = {
        "Entry": {
            "recognition": "DirectHit",
            "next": ["Match"],
            "timeout": 100,
            "rate_limit": 10,
            "pre_delay": 0,
            "post_delay": 0,
        },
        "Match": {
            "recognition": "TemplateMatch",
            "template": "coordinate-test.png",
            "threshold": 0.99,
            "pre_delay": 0,
            "post_delay": 0,
        },
    }
    try:
        detail = tasker.post_task("Entry", pipeline).wait().get()
        matches = [
            n
            for n in detail.nodes
            if n.name == "Match" and n.recognition and n.recognition.hit
        ]
        assert bool(matches) == (not raw_mode)
    finally:
        tasker.post_stop().wait()


@pytest.mark.integration
def test_screencap_metadata_through_mcp(monkeypatch, tmp_path, image_controller):
    from fastmcp import Client

    cid, _ = image_controller
    monkeypatch.setattr(vision, "get_screenshots_dir", lambda: tmp_path)

    async def call_tool():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schemas = {
                name: (getattr(tool, "input_schema", None) or tool.inputSchema)[
                    "properties"
                ]
                for name, tool in tools.items()
            }
            params = schemas["screencap"]
            assert params["resolution"]["default"] is None
            assert params["include_metadata"]["default"] is False
            for name in ("connect_window", "connect_adb_device"):
                assert schemas[name]["target_short_side"]["default"] == 720
            result = await client.call_tool(
                "screencap", {"controller_id": cid, "include_metadata": True}
            )
            info = json.loads(result.content[0].text)
            assert info["coordinate_size"] == [1280, 720]
            assert info["image_size"] == [1280, 720]
            assert info["image_to_controller"] == {
                "scale": [1.0, 1.0],
                "offset": [0.0, 0.0],
            }
            assert Path(info["path"]).is_file()

    asyncio.run(call_tool())


@pytest.mark.integration
def test_ocr_region_contract_without_model(monkeypatch, image_controller):
    cid, controller = image_controller
    controller.image = np.zeros((720, 1280, 3), dtype=np.uint8)
    controller.image[130:170, 120:210] = (0, 255, 0)
    controller.image[250:290, 240:340] = (0, 255, 0)
    resource = Resource()
    assert (
        resource.post_bundle(Path(__file__).parent / "fixtures" / "bundle_minimal")
        .wait()
        .succeeded
    )
    tasker = Tasker()
    tasker.bind(resource, controller)

    def recognize_region(reco_type, param, image):
        assert reco_type == JRecognitionType.OCR
        # OCR and ColorMatch share native ROI handling; avoid model weights in CI.
        return tasker.post_recognition(
            JRecognitionType.ColorMatch,
            JColorMatch(
                lower=[[0, 255, 0]],
                upper=[[0, 255, 0]],
                connected=True,
                roi=param.roi,
                roi_offset=param.roi_offset,
            ),
            image,
        )

    adapter = SimpleNamespace(post_recognition=recognize_region)
    monkeypatch.setattr(vision, "get_or_create_tasker", lambda _: adapter)
    monkeypatch.setattr(vision, "check_ocr_files_exist", lambda: True)
    try:
        results = vision._ocr_impl(cid, region=(100, 100, 250, 100))
        assert isinstance(results, list)
        assert [tuple(result.box) for result in results] == [(120, 130, 90, 40)]
    finally:
        tasker.post_stop().wait()


@pytest.mark.integration
def test_region_ocr_excludes_neighbor_and_returns_global_boxes(
    monkeypatch, image_controller
):
    resource_dir = get_resource_dir()
    model_dir = resource_dir / "model" / "ocr"
    if not all(
        (model_dir / name).is_file() for name in ("det.onnx", "rec.onnx", "keys.txt")
    ):
        pytest.skip("Local OCR model is not installed")

    cid, controller = image_controller
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    cv2.putText(image, "TARGET", (120, 155), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(image, "DECOY", (240, 260), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    controller.image = image

    resource = Resource()
    assert resource.post_bundle(resource_dir).wait().succeeded
    tasker = Tasker()
    tasker.bind(resource, controller)
    monkeypatch.setattr(vision, "get_or_create_tasker", lambda _: tasker)
    try:
        results = vision._ocr_impl(cid, region=(100, 100, 250, 100))
        assert isinstance(results, list)
        assert [result.text for result in results] == ["TARGET"]
        x, y, width, height = results[0].box
        assert 100 <= x < x + width <= 350
        assert 100 <= y < y + height <= 200
    finally:
        tasker.post_stop().wait()
