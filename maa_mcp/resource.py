from pathlib import Path
from typing import Optional

from maa.controller import Controller
from maa.resource import Resource
from maa.tasker import Tasker

from maa_mcp.core import mcp, object_registry


@mcp.tool(
    name="load_resource",
    description="""
    加载 MAA 资源包，包含 OCR 模型、图像模板等自动化所需资源。

    参数：
    - resource_path: 资源包根目录路径（字符串）
      - 路径应指向包含 resource/model/*.onnx 的目录层级
      - 典型路径示例：项目根目录下的 assets/resource
      - 传入路径为 resource 这一级目录，而非其子目录

    返回值：
    - 成功：返回资源 ID（字符串），用于后续 OCR 等操作
    - 失败：返回 None（路径不存在或资源加载失败）

    前置检查：
    调用前应验证路径存在性，若路径不存在，需提示用户先配置资源文件。
""",
)
def load_resource(resource_path: str) -> Optional[str]:
    if not Path(resource_path).exists():
        return None
    resource = Resource()
    if not resource.post_bundle(resource_path).wait().succeeded:
        return None
    return object_registry.register(resource)


def get_or_create_tasker(controller_id: str, resource_id: str) -> Optional[Tasker]:
    """
    根据 controller_id 和 resource_id 获取或创建 tasker 实例。
    tasker 会被缓存，相同组合不会重复创建。
    """
    tasker_cache_key = f"_tasker_{controller_id}_{resource_id}"
    tasker: Tasker | None = object_registry.get(tasker_cache_key)
    if tasker:
        return tasker

    controller: Controller | None = object_registry.get(controller_id)
    resource: Resource | None = object_registry.get(resource_id)
    if not controller or not resource:
        return None

    tasker = Tasker()
    tasker.bind(resource, controller)
    if not tasker.inited:
        return None

    object_registry.register_by_name(tasker_cache_key, tasker)
    return tasker

