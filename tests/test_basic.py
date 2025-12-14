"""基础冒烟测试 - 确保包能正常导入且基础功能可用"""

import pytest


class TestImport:
    """测试包导入"""

    def test_import_package(self):
        """测试能否导入主包"""
        import maa_mcp

        assert maa_mcp.__version__ is not None
        assert maa_mcp.__author__ is not None

    def test_import_paths(self):
        """测试 paths 模块导入和基本功能"""
        from maa_mcp.paths import (
            get_data_dir,
            get_resource_dir,
            get_model_dir,
            get_ocr_dir,
            get_config_dir,
            get_debug_dir,
            get_screenshots_dir,
        )

        # 验证路径函数返回 Path 对象
        from pathlib import Path

        assert isinstance(get_data_dir(), Path)
        assert isinstance(get_resource_dir(), Path)
        assert isinstance(get_model_dir(), Path)
        assert isinstance(get_ocr_dir(), Path)
        assert isinstance(get_config_dir(), Path)
        assert isinstance(get_debug_dir(), Path)
        assert isinstance(get_screenshots_dir(), Path)

        # 验证路径层级关系
        assert get_resource_dir().parent == get_data_dir()
        assert get_model_dir().parent == get_resource_dir()
        assert get_ocr_dir().parent == get_model_dir()


class TestRegistry:
    """测试对象注册表"""

    def test_register_and_get(self):
        """测试注册和获取对象"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        obj = {"test": "value"}
        obj_id = registry.register(obj)

        assert obj_id is not None
        assert registry.get(obj_id) is obj

    def test_register_by_name(self):
        """测试按名称注册"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        obj = {"test": "value"}
        name = "my_object"
        returned_name = registry.register_by_name(name, obj)

        assert returned_name == name
        assert registry.get(name) is obj

    def test_unregister(self):
        """测试注销对象"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        obj = {"test": "value"}
        obj_id = registry.register(obj)

        assert registry.unregister(obj_id) is True
        assert registry.get(obj_id) is None
        assert registry.unregister(obj_id) is False  # 再次注销应返回 False

    def test_list_and_count(self):
        """测试列出和计数"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        assert registry.count() == 0

        id1 = registry.register("obj1")
        id2 = registry.register("obj2")

        assert registry.count() == 2
        assert id1 in registry.list()
        assert id2 in registry.list()

    def test_exists(self):
        """测试存在性检查"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        obj_id = registry.register("test")

        assert registry.exists(obj_id) is True
        assert registry.exists("nonexistent") is False

    def test_clear(self):
        """测试清空注册表"""
        from maa_mcp.registry import ObjectRegistry

        registry = ObjectRegistry()
        registry.register("obj1")
        registry.register("obj2")

        registry.clear()
        assert registry.count() == 0


class TestCore:
    """测试核心模块 - 需要 maafw 可用"""

    def test_import_core(self):
        """测试能否导入 core 模块"""
        from maa_mcp.core import mcp, object_registry

        assert mcp is not None
        assert object_registry is not None

    def test_mcp_server_config(self):
        """测试 MCP 服务器配置"""
        from maa_mcp.core import mcp

        assert mcp.name == "MaaMCP"

    def test_import_main(self):
        """测试能否导入 main 模块（包含所有工具注册）"""
        from maa_mcp.main import mcp

        assert mcp is not None
