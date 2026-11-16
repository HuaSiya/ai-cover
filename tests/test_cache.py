"""音频缓存测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cache import AudioCache  # noqa: E402


class AudioCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = AudioCache(directory, max_bytes=1024 * 1024, expire_seconds=3600)
            await cache.initialize()
            data = b"ID3" + b"x" * 2048
            await cache.put("BV1ZJ4A6gEkf", data, "mp3")

            cached = await cache.get("BV1ZJ4A6gEkf")
            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(cached.data, data)
            self.assertEqual(cached.extension, "mp3")

    async def test_get_missing_and_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = AudioCache(directory, max_bytes=1024 * 1024, expire_seconds=3600)
            await cache.initialize()
            self.assertIsNone(await cache.get("BV1ZJ4A6gEkf"))
            self.assertIsNone(await cache.get("../etc/passwd"))
            await cache.put("../etc/passwd", b"x" * 2048, "m4a")
            self.assertIsNone(await cache.get("BV1ZJ4A6gEkf"))

    async def test_capacity_eviction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = AudioCache(directory, max_bytes=4096, expire_seconds=3600)
            await cache.initialize()
            await cache.put("BV1ZJ4A6gEkf", b"a" * 2048, "m4a")
            await cache.put("BV1aVS9YqE1U", b"b" * 2048, "m4a")
            files = [path for path in Path(directory).iterdir() if path.is_file()]
            total = sum(path.stat().st_size for path in files)
            self.assertLessEqual(total, 4096)


if __name__ == "__main__":
    unittest.main()
