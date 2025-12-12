import uuid
from typing import Any, Optional


class ObjectRegistry:
    def __init__(self):
        """初始化对象注册表"""
        self._objects: dict[str, Any] = {}

    def register(self, obj: Any) -> str:
        object_id = str(uuid.uuid4())
        self._objects[object_id] = obj
        return object_id

    def register_by_name(self, name: str, obj: Any) -> str:
        self._objects[name] = obj
        return name

    def get(self, object_id: str) -> Optional[Any]:
        return self._objects.get(object_id)

    def unregister(self, object_id: str) -> bool:
        if object_id in self._objects:
            del self._objects[object_id]
            return True
        return False

    def list(self) -> list[str]:
        return list(self._objects.keys())

    def clear(self) -> None:
        self._objects.clear()

    def count(self) -> int:
        return len(self._objects)

    def exists(self, object_id: str) -> bool:
        return object_id in self._objects
