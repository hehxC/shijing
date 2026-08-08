import tempfile
import unittest
from pathlib import Path

from app.service.image_store import (
    LocalImageStore,
    bytes_to_data_url,
    data_url_to_bytes,
)


class ImageStoreTests(unittest.TestCase):
    def setUp(self):
        """每个用例用独立临时目录，互不干扰。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalImageStore(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_get_delete_roundtrip(self):
        key = self.store.save("materials/20260807/abc.jpg", b"\xff\xd8image-bytes")
        self.assertTrue(self.store.exists(key))
        self.assertEqual(b"\xff\xd8image-bytes", self.store.get(key))
        self.store.delete(key)
        self.assertFalse(self.store.exists(key))
        # 删除不存在的 key 不报错
        self.store.delete("materials/20260807/not-exist.jpg")

    def test_nested_directories_are_supported(self):
        key = self.store.save("generated/nested/deep.png", b"png-bytes")
        self.assertEqual(b"png-bytes", self.store.get(key))

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save("../escape.png", b"x")
        with self.assertRaises(ValueError):
            self.store.get("../../etc/passwd")

    def test_data_url_helpers_roundtrip(self):
        data_url = bytes_to_data_url("photo.jpg", b"\xff\xd8hello")
        data, mime = data_url_to_bytes(data_url)
        self.assertEqual(b"\xff\xd8hello", data)
        self.assertEqual("image/jpeg", mime)
        with self.assertRaises(ValueError):
            data_url_to_bytes("not-a-data-url")


if __name__ == "__main__":
    unittest.main()
