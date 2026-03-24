from typing import Optional

from loguru import logger
from maa.controller import Controller
from maa.resource import Resource
from maa.tasker import Tasker

from maa_mcp.core import object_registry
from maa_mcp.paths import get_resource_dir


# 全局资源 ID 的固定键名
_GLOBAL_RESOURCE_KEY = "_global_resource"

# 资源路径列表（按加载顺序，后加载的会覆盖先加载的同名资源）
_resource_paths: list[str] = []
# 记录默认路径是否已加载
_default_loaded: bool = False
# 记录已加载的自定义路径（用于去重）
_loaded_paths: list[str] = []


def add_resource_path(path: str):
    """
    添加资源路径，后加载的会覆盖先加载的同名资源。
    添加后会立即加载该路径到已创建的 Resource。
    """
    global _resource_paths, _default_loaded, _loaded_paths

    if path not in _resource_paths:
        _resource_paths.append(path)

    resource: Resource | None = object_registry.get(_GLOBAL_RESOURCE_KEY)
    if not resource:
        # Resource 还不存在，等 get_or_create_resource 时一起加载
        return

    # Resource 已存在，立即加载新路径
    # 先确保默认路径已加载
    if not _default_loaded:
        default_path = str(get_resource_dir())
        if not resource.post_bundle(default_path).wait().succeeded:
            logger.warning(f"加载默认资源包失败: {default_path}")
        _default_loaded = True

    # 只加载新增的自定义路径（去重）
    if path not in _loaded_paths:
        if not resource.post_bundle(str(path)).wait().succeeded:
            logger.warning(f"加载自定义资源包失败: {path}")
        _loaded_paths.append(path)


def clear_resource():
    """清除全局 Resource 缓存，强制重新创建（保留已配置的资源路径）。"""
    global _default_loaded, _loaded_paths
    _default_loaded = False
    _loaded_paths.clear()
    object_registry.unregister(_GLOBAL_RESOURCE_KEY)


def get_or_create_resource() -> Optional[Resource]:
    """
    获取或创建全局唯一的 Resource 实例。
    注意：调用此函数前应确保 OCR 资源已存在，否则可能加载失败。

    Resource 会按顺序加载多个资源路径，后加载的会覆盖先加载的同名资源。
    """
    global _resource_paths, _default_loaded, _loaded_paths

    resource: Resource | None = object_registry.get(_GLOBAL_RESOURCE_KEY)
    if not resource:
        resource = Resource()
        object_registry.register_by_name(_GLOBAL_RESOURCE_KEY, resource)

        # 首次创建时加载所有路径
        default_path = str(get_resource_dir())
        if not resource.post_bundle(default_path).wait().succeeded:
            logger.warning(f"加载默认资源包失败: {default_path}")
        _default_loaded = True

        for path in _resource_paths:
            if not resource.post_bundle(str(path)).wait().succeeded:
                logger.warning(f"加载自定义资源包失败: {path}")
            _loaded_paths.append(path)

    return resource


def get_or_create_tasker(controller_id: str) -> Optional[Tasker]:
    """
    根据 controller_id 获取或创建 tasker 实例。
    会自动加载全局资源。tasker 会被缓存，相同 controller 不会重复创建。
    """
    tasker_cache_key = f"_tasker_{controller_id}"
    tasker: Tasker | None = object_registry.get(tasker_cache_key)
    if tasker:
        return tasker

    controller: Controller | None = object_registry.get(controller_id)
    resource = get_or_create_resource()
    if not controller or not resource:
        return None

    tasker = Tasker()
    tasker.bind(resource, controller)
    if not tasker.inited:
        return None

    object_registry.register_by_name(tasker_cache_key, tasker)
    return tasker
