"""vision 模块单元测试

测试纯函数（不需要 controller / 设备）：
- _resize_short_edge: 短边归一化的各种长宽比
- _scale_region_to_image: region 在 raw → new 坐标系下的缩放
- _crop_region: 边界 clamp
- _apply_screencap_pipeline: 完整流水线（resize + region 缩放 + 裁剪）端到端
- _save_captured_image / _validate_path_segment: 把裁图存到 MaaFramework bundle image 目录
"""

import cv2
import numpy as np
import pytest
from pathlib import Path

from maa_mcp.vision import (
    _apply_screencap_pipeline,
    _crop_region,
    _resize_short_edge,
    _save_captured_image,
    _scale_region_to_image,
    _validate_path_segment,
)


# ---------------------------------------------------------------------------
# _resize_short_edge
# ---------------------------------------------------------------------------


class TestResizeShortEdge:
    def test_landscape_1080p_to_720p(self):
        """1920×1080 → 1280×720"""
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (720, 1280, 3)

    def test_portrait_1080p_to_720p(self):
        """1080×1920 → 720×1280"""
        img = np.zeros((1920, 1080, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (1280, 720, 3)

    def test_already_720p_noop(self):
        """1280×720 → 1280×720（返回原对象，no-op）"""
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (720, 1280, 3)
        assert out is img  # short edge 已等于 target，原对象返回

    def test_non_16_9_preserved(self):
        """1280×800 (16:10) → 1152×720（短边 800 → 720，等比）"""
        img = np.zeros((800, 1280, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (720, 1152, 3)

    def test_5_4_aspect_preserved(self):
        """1280×1024 (5:4) → 900×720（短边 1024 → 720，等比）"""
        img = np.zeros((1024, 1280, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (720, 900, 3)

    def test_2k_landscape_to_720p(self):
        """2560×1440 (2K 横屏) → 1280×720"""
        img = np.zeros((1440, 2560, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (720, 1280, 3)

    def test_2k_portrait_to_720p(self):
        """1440×2560 (2K 竖屏) → 720×1280"""
        img = np.zeros((2560, 1440, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 720)
        assert out.shape == (1280, 720, 3)

    def test_custom_resolution_1080p(self):
        """自定义短边 1080：1920×1080 → 1920×1080（no-op）"""
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        out = _resize_short_edge(img, 1080)
        assert out is img

    def test_invalid_resolution_raises(self):
        """resolution <= 0 抛 ValueError"""
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="必须 > 0"):
            _resize_short_edge(img, 0)
        with pytest.raises(ValueError, match="必须 > 0"):
            _resize_short_edge(img, -1)


# ---------------------------------------------------------------------------
# _scale_region_to_image
# ---------------------------------------------------------------------------


class TestScaleRegionToImage:
    def test_same_shape_noop(self):
        """raw 和 new 形状一致：原样返回"""
        region = (100, 200, 300, 400)
        out = _scale_region_to_image(region, (1080, 1920), (1080, 1920))
        assert out == region

    def test_1080p_to_720p_landscape(self):
        """1920×1080 raw → 1280×720，region 缩放 2/3"""
        # raw 坐标 (100, 100, 800, 600) → new 坐标 (66.67, 66.67, 533.33, 400)
        out = _scale_region_to_image(
            (100, 100, 800, 600), (1080, 1920), (720, 1280)
        )
        assert out[0] == pytest.approx(100 * 1280 / 1920)
        assert out[1] == pytest.approx(100 * 720 / 1080)
        assert out[2] == pytest.approx(800 * 1280 / 1920)
        assert out[3] == pytest.approx(600 * 720 / 1080)

    def test_1080p_portrait_to_720p(self):
        """1080×1920 raw → 720×1280，region 缩放 2/3"""
        out = _scale_region_to_image(
            (100, 100, 800, 600), (1920, 1080), (1280, 720)
        )
        assert out[0] == pytest.approx(100 * 720 / 1080)
        assert out[1] == pytest.approx(100 * 1280 / 1920)
        assert out[2] == pytest.approx(800 * 720 / 1080)
        assert out[3] == pytest.approx(600 * 1280 / 1920)

    def test_non_uniform_aspect(self):
        """非等比缩放：1280×800 (16:10) → 1152×720"""
        out = _scale_region_to_image(
            (100, 50, 600, 400), (800, 1280), (720, 1152)
        )
        # scale_x = 1152/1280 = 0.9, scale_y = 720/800 = 0.9
        assert out == pytest.approx((90, 45, 540, 360))


# ---------------------------------------------------------------------------
# _crop_region
# ---------------------------------------------------------------------------


class TestCropRegion:
    def test_none_returns_original(self):
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        out = _crop_region(img, None)
        assert out is img

    def test_basic_crop(self):
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        out = _crop_region(img, (10, 20, 50, 30))
        assert out.shape == (30, 50, 3)

    def test_clamp_to_boundary(self):
        """超出边界的 region 被 clamp"""
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        out = _crop_region(img, (180, 90, 100, 50))
        # x=180 clamp 到 199，w=100 clamp 到 200-180=20
        # y=90 clamp 到 99，h=50 clamp 到 100-90=10
        assert out.shape == (10, 20, 3)

    def test_float_region_supported(self):
        """浮点 region 也能裁（依赖 int() 截断转换）"""
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        # int() 截断：int(10.7)=10, int(50.6)=50, int(30.4)=30
        out = _crop_region(img, (10.7, 20.3, 50.6, 30.4))
        assert out.shape == (30, 50, 3)


# ---------------------------------------------------------------------------
# _apply_screencap_pipeline —— 完整流水线（真实 numpy 输入，无 mock）
# ---------------------------------------------------------------------------


class TestApplyScreencapPipeline:
    """用真实 numpy 数组测 screencap 的图像处理流水线。

    真实场景：AI 拿到 screencap 截图后想做局部 OCR，需要传 region。
    region 坐标空间 = 设备原始分辨率；流水线负责缩放到输出图坐标系。
    """

    def test_default_720p_no_region(self):
        """raw 1920×1080 + 无 region + 默认 720：全图归一到 1280×720"""
        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(raw, region=None, resolution=720)
        assert out.shape == (720, 1280, 3)

    def test_default_720p_portrait(self):
        """raw 1080×1920 + 无 region + 默认 720：全图归一到 720×1280"""
        raw = np.zeros((1920, 1080, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(raw, region=None, resolution=720)
        assert out.shape == (1280, 720, 3)

    def test_region_in_raw_coords_1080p(self):
        """raw 1920×1080 + region (in 1080p 空间) + 默认 720：
        region 按 2/3 缩放后裁到 1280×720 上 → 输出 (400, 533, 3)
        """
        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # raw 坐标 (100, 100, 800, 600) → new 坐标 (66.67, 66.67, 533.33, 400)
        # int 截断后裁到 1280×720 上 → shape (400, 533, 3)
        out = _apply_screencap_pipeline(
            raw, region=(100, 100, 800, 600), resolution=720
        )
        assert out.shape == (400, 533, 3)

    def test_region_in_raw_coords_portrait(self):
        """raw 1080×1920 + region (in 竖屏 1080p 空间) + 默认 720：
        region 按 2/3 缩放后裁到 720×1280 上 → 输出 (400, 533, 3)
        """
        raw = np.zeros((1920, 1080, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(
            raw, region=(100, 100, 800, 600), resolution=720
        )
        assert out.shape == (400, 533, 3)

    def test_region_no_scale_when_already_720p(self):
        """raw 1280×720 + region + 默认 720：缩放=1，no-op"""
        raw = np.zeros((720, 1280, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(
            raw, region=(100, 100, 800, 600), resolution=720
        )
        assert out.shape == (600, 800, 3)

    def test_resolution_none_passthrough_region(self):
        """raw 1920×1080 + region + resolution=None：
        不归一化、不缩放 region，region 直接应用到 1920×1080。
        """
        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(
            raw, region=(100, 100, 800, 600), resolution=None
        )
        assert out.shape == (600, 800, 3)

    def test_resolution_none_passthrough_full(self):
        """raw 1920×1080 + 无 region + resolution=None：原图透传"""
        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(raw, region=None, resolution=None)
        assert out.shape == (1080, 1920, 3)

    def test_region_without_resolution_uses_raw_coords_directly(self):
        """raw 1280×720 + region (in 720p 空间) + resolution=None：
        region 直接在 raw 空间裁，无缩放。
        """
        raw = np.zeros((720, 1280, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(
            raw, region=(100, 100, 200, 150), resolution=None
        )
        assert out.shape == (150, 200, 3)

    def test_non_16_9_device_normalized(self):
        """raw 1280×800 (16:10) + region + 720p：
        设备归一到 1152×720；region 按 (1152/1280, 720/800) = (0.9, 0.9) 缩放。
        """
        raw = np.zeros((800, 1280, 3), dtype=np.uint8)
        out = _apply_screencap_pipeline(
            raw, region=(100, 50, 600, 400), resolution=720
        )
        # scaled region: (90, 45, 540, 360) → 裁出 (360, 540, 3)
        assert out.shape == (360, 540, 3)


# ---------------------------------------------------------------------------
# _apply_screencap_pipeline 集成 cv2.imwrite → 落盘链路
# ---------------------------------------------------------------------------


class TestScreencapSaveRoundtrip:
    """验证流水线输出能正确被 cv2.imwrite → cv2.imread 还原，shape 不丢。

    模拟 _screencap 内部"流水线 + imwrite + imread"的核心环节。
    """

    @pytest.mark.parametrize(
        "raw_shape,region,resolution,expected_shape",
        [
            # 默认 720p 全图
            ((1080, 1920, 3), None, 720, (720, 1280, 3)),
            # 默认 720p 竖屏
            ((1920, 1080, 3), None, 720, (1280, 720, 3)),
            # region + 720p 横屏
            ((1080, 1920, 3), (100, 100, 800, 600), 720, (400, 533, 3)),
            # region + 720p 竖屏
            ((1920, 1080, 3), (100, 100, 800, 600), 720, (400, 533, 3)),
            # resolution=None
            ((1080, 1920, 3), None, None, (1080, 1920, 3)),
        ],
    )
    def test_roundtrip_shape(
        self, tmp_path, raw_shape, region, resolution, expected_shape
    ):
        raw = np.zeros(raw_shape, dtype=np.uint8)
        processed = _apply_screencap_pipeline(raw, region, resolution)

        # 落盘
        filepath = tmp_path / "test.png"
        assert cv2.imwrite(str(filepath), processed)

        # 读回验证 shape
        loaded = cv2.imread(str(filepath))
        assert loaded.shape == expected_shape


# ---------------------------------------------------------------------------
# _validate_path_segment
# ---------------------------------------------------------------------------


class TestValidatePathSegment:
    """校验 subcategory / name 等路径段：非空、不含 ..、不是绝对路径。"""

    def test_normal_string_returned_stripped(self):
        assert _validate_path_segment("UI", "subcategory") == "UI"
        assert _validate_path_segment("  UI  ", "subcategory") == "UI"

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="不能为空"):
            _validate_path_segment("", "name")
        with pytest.raises(ValueError, match="不能为空"):
            _validate_path_segment("   ", "name")

    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="必须是 str"):
            _validate_path_segment(None, "name")  # type: ignore[arg-type]

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match=r"不能包含 '\.\.' 段"):
            _validate_path_segment("../foo", "subcategory")
        with pytest.raises(ValueError, match=r"不能包含 '\.\.' 段"):
            _validate_path_segment("../../etc/passwd", "name")
        with pytest.raises(ValueError, match=r"不能包含 '\.\.' 段"):
            _validate_path_segment("foo/../bar", "name")

    def test_absolute_path_rejected(self):
        # "/abs/path" 先被前导分隔符检查拦截（更具体的错误）
        with pytest.raises(ValueError, match="不能以路径分隔符开头"):
            _validate_path_segment("/abs/path", "name")

    def test_windows_drive_letter_rejected(self):
        # 在 Windows 上 Path("C:\\foo").is_absolute() == True，已被绝对路径分支兜住
        # 这里只保证它抛 ValueError（不被当作合法相对路径接受）
        with pytest.raises(ValueError):
            _validate_path_segment("C:\\foo", "name")

    def test_leading_separator_rejected(self):
        """以 / 或 \\ 开头的串视为可疑（Windows 上 /abs 是相对路径但意图是绝对）。"""
        with pytest.raises(ValueError, match="不能以路径分隔符开头"):
            _validate_path_segment("/leading", "name")
        with pytest.raises(ValueError, match="不能以路径分隔符开头"):
            _validate_path_segment("\\leading", "name")


# ---------------------------------------------------------------------------
# _save_captured_image
# ---------------------------------------------------------------------------


def _write_test_png(path, shape=(32, 64, 3)):
    """写一张全黑 PNG 到 path，返回 path。"""
    img = np.zeros(shape, dtype=np.uint8)
    assert cv2.imwrite(str(path), img)
    return path


class TestSaveCapturedImage:
    """_save_captured_image 把截图存到 <bundle_root>/image/<sub>/<name>.png。"""

    def test_happy_path(self, tmp_path):
        # Arrange：tmp bundle_root + 一张输入 PNG
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png", shape=(40, 80, 3))

        # Act
        result = _save_captured_image(
            captured_path=str(src),
            bundle_root=bundle_root,
            subcategory="UI",
            name="MyButton",
        )

        # Assert：返回路径正确 + 文件存在 + shape 还原
        expected = (bundle_root / "image" / "UI" / "MyButton.png").absolute()
        assert result == str(expected)
        assert expected.is_file()
        loaded = cv2.imread(str(expected))
        assert loaded.shape == (40, 80, 3)

    def test_creates_parent_dirs(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        result = _save_captured_image(
            str(src), bundle_root, "Deep/Nested/Cat", "X"
        )
        target = Path(result)
        assert target.is_file()
        assert target.parent == (bundle_root / "image" / "Deep" / "Nested" / "Cat").absolute()

    def test_existing_file_no_overwrite_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        # 先写一次
        _save_captured_image(str(src), bundle_root, "UI", "Dup")
        # 再写一次（默认 overwrite=False）→ 抛 ValueError
        with pytest.raises(ValueError, match="目标已存在"):
            _save_captured_image(str(src), bundle_root, "UI", "Dup")

    def test_existing_file_overwrite_succeeds(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png", shape=(10, 10, 3))
        first_path = _save_captured_image(str(src), bundle_root, "UI", "Dup")
        # 写一张不同的图
        src2 = _write_test_png(tmp_path / "source2.png", shape=(20, 30, 3))
        result = _save_captured_image(
            str(src2), bundle_root, "UI", "Dup", overwrite=True
        )
        assert result == first_path
        loaded = cv2.imread(result)
        assert loaded.shape == (20, 30, 3)

    def test_traversal_in_name_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        with pytest.raises(ValueError, match=r"'\.\.' 段"):
            _save_captured_image(str(src), bundle_root, "UI", "../../etc/passwd")

    def test_traversal_in_subcategory_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        with pytest.raises(ValueError, match=r"'\.\.' 段"):
            _save_captured_image(str(src), bundle_root, "../foo", "X")

    def test_absolute_name_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        # "/abs" 以分隔符开头，先被前导分隔符检查拦截
        with pytest.raises(ValueError, match="不能以路径分隔符开头"):
            _save_captured_image(str(src), bundle_root, "UI", "/abs")

    def test_empty_subcategory_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        with pytest.raises(ValueError, match="不能为空"):
            _save_captured_image(str(src), bundle_root, "", "X")

    def test_empty_name_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        src = _write_test_png(tmp_path / "source.png")
        with pytest.raises(ValueError, match="不能为空"):
            _save_captured_image(str(src), bundle_root, "UI", "")

    def test_missing_captured_path_rejected(self, tmp_path):
        bundle_root = tmp_path / "bundle"
        nonexistent = tmp_path / "does_not_exist.png"
        with pytest.raises(ValueError, match="不是有效文件"):
            _save_captured_image(str(nonexistent), bundle_root, "UI", "X")

    def test_bundle_root_does_not_exist_creates(self, tmp_path):
        """bundle_root 路径不存在时自动创建（user-friendly）。"""
        bundle_root = tmp_path / "newly_created_bundle"
        assert not bundle_root.exists()
        src = _write_test_png(tmp_path / "source.png")
        result = _save_captured_image(str(src), bundle_root, "UI", "X")
        assert bundle_root.is_dir()
        assert Path(result).is_file()
