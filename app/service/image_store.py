"""统一图片存储抽象层。

业务代码只依赖 ImageStore 接口（save/get/delete/exists）。当前提供两种实现：

- ``local``：本地文件系统（开发/单机试用），根目录 ``data/images/``；
- ``s3``：S3 兼容对象存储（AWS S3 / 阿里云 OSS / MinIO），通过 ``IMAGE_STORE=s3``
  及 ``S3_*`` 环境变量切换，桶需要预先创建。

boto3 采用惰性导入，未使用对象存储时不引入该依赖。
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


class S3ImageStore(ImageStore):
    """S3 兼容对象存储实现，可对接 AWS S3、阿里云 OSS 与 MinIO。

    桶需要预先创建；``prefix`` 用于在同一桶内隔离不同环境/实例的数据。
    """

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        bucket: str,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        prefix: str = "",
    ):
        if not bucket:
            raise ValueError("S3_BUCKET 不能为空")
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        client_kwargs: dict = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if region:
            client_kwargs["region_name"] = region
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        self.client = boto3.client("s3", **client_kwargs)

    def _full_key(self, key: str) -> str:
        if not self.prefix:
            return key
        return f"{self.prefix}/{key}"

    def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(
            Bucket=self.bucket, Key=self._full_key(key), Body=data, **extra
        )
        return key

    def get(self, key: str) -> bytes:
        response = self.client.get_object(
            Bucket=self.bucket, Key=self._full_key(key)
        )
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._full_key(key))

    def exists(self, key: str) -> bool:
        import botocore

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise


def get_image_store() -> ImageStore:
    """按配置返回存储实现（默认本地文件系统）。"""
    mode = os.getenv("IMAGE_STORE", "local")
    if mode == "local":
        root = Path(os.getenv("IMAGE_STORE_ROOT", "data/images"))
        return LocalImageStore(root)
    if mode == "s3":
        return S3ImageStore(
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            bucket=os.getenv("S3_BUCKET", ""),
            access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID"),
            secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
            region=os.getenv("S3_REGION") or os.getenv("AWS_REGION"),
            prefix=os.getenv("S3_PREFIX", ""),
        )
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
