"""统一图片存储抽象层。

业务代码只依赖 ImageStore 接口（save/get/delete/exists），
当前默认实现是本地文件系统（data/images/），以后切对象存储（OSS/MinIO）
只需新增一个 S3 兼容实现并在 IMAGE_STORE 配置里切换。
"""

import base64
import mimetypes
import os
from abc import ABC, abstractmethod
from pathlib import Path


class ImageStore(ABC):
    """图片存储接口：以对象 key（相对路径）读写字节。"""

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """保存图片字节，返回对象 key。"""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """按 key 读取图片字节。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除图片（不存在时静默）。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """判断 key 对应的图片是否存在。"""


class LocalImageStore(ImageStore):
    """本地文件系统实现：根目录下按 key 存文件。"""

    def __init__(self, root: Path):
        self.root = root

    def _resolve(self, key: str) -> Path:
        """把 key 解析成根目录内的绝对路径，防止路径穿越。"""
        path = (self.root / key).resolve()
        root = self.root.resolve()
        # key 只能落在根目录内部（允许一层或多层子目录）
        if path.parent != root and root not in path.parents:
            raise ValueError(f"非法的图片 key：{key}")
        return path

    def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()


def get_image_store() -> ImageStore:
    """按配置返回存储实现（默认本地文件系统）。"""
    mode = os.getenv("IMAGE_STORE", "local")
    if mode == "local":
        root = Path(os.getenv("IMAGE_STORE_ROOT", "data/images"))
        return LocalImageStore(root)
    raise ValueError(f"不支持的 IMAGE_STORE 配置：{mode}")


def data_url_to_bytes(data_url: str) -> tuple[bytes, str]:
    """把 data URL 解码为 (字节, mime 类型)，失败抛 ValueError。"""
    try:
        header, data = data_url.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[1]
    except (ValueError, IndexError) as exc:
        raise ValueError("图片必须是有效的 Data URL") from exc
    if not mime_type.startswith("image/") or not data:
        raise ValueError("图片必须是有效的 Data URL")
    try:
        return base64.b64decode(data, validate=True), mime_type
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise ValueError("图片数据无效") from exc


def bytes_to_data_url(key: str, data: bytes) -> str:
    """把存储字节还原成 data URL（供前端展示/视觉模型使用）。"""
    mime_type = mimetypes.guess_type(key)[0] or "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
