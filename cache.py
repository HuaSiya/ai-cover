"""插件私有音频缓存。

QQ 语音每次都要把音频 base64 传给宿主，重复点同一首歌时没必要反复下载。
缓存按 BV 号命名，带过期时间和总容量上限；所有阻塞文件操作都放到线程里。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

_BVID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
_SAFE_EXTENSIONS = {"m4a", "mp3", "mp4", "ogg", "wav", "flac", "aac", "amr", "silk"}


@dataclass(frozen=True)
class CachedAudio:
    """一次缓存命中。"""

    data: bytes
    extension: str
    path: Path


class AudioCache:
    """磁盘音频缓存。"""

    def __init__(
        self,
        root: Path | str,
        *,
        max_bytes: int,
        expire_seconds: int,
    ) -> None:
        self.root = Path(root).expanduser()
        self.max_bytes = max(1, int(max_bytes))
        self.expire_seconds = max(60, int(expire_seconds))
        self._put_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """创建目录并清理过期文件。"""

        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for path in list(self.root.iterdir()):
            if not path.is_file():
                continue
            try:
                if path.name.endswith(".part"):
                    if now - path.stat().st_mtime > 3600:
                        path.unlink(missing_ok=True)
                    continue
                if now - path.stat().st_mtime > self.expire_seconds:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        self._enforce_size_sync()

    async def get(self, bvid: str) -> CachedAudio | None:
        """读取缓存；不存在、过期或损坏时返回 ``None``。"""

        return await asyncio.to_thread(self._get_sync, bvid)

    def _get_sync(self, bvid: str) -> CachedAudio | None:
        if not _BVID_PATTERN.fullmatch(str(bvid or "")):
            return None
        try:
            candidates = list(self.root.glob(f"{bvid}.*"))
        except OSError:
            return None

        now = time.time()
        for path in candidates:
            try:
                if path.suffix == ".part" or not path.is_file():
                    continue
                if path.is_symlink():
                    path.unlink(missing_ok=True)
                    continue
                if now - path.stat().st_mtime > self.expire_seconds:
                    path.unlink(missing_ok=True)
                    continue
                data = path.read_bytes()
                if len(data) < 512:
                    path.unlink(missing_ok=True)
                    continue
                os.utime(path, None)
                extension = path.suffix.lstrip(".").lower() or "m4a"
                return CachedAudio(data=data, extension=extension, path=path)
            except OSError:
                continue
        return None

    async def put(self, bvid: str, data: bytes, extension: str) -> None:
        """写入缓存；先写 ``.part`` 再原子替换，避免读到半截文件。"""

        if not _BVID_PATTERN.fullmatch(str(bvid or "")) or len(data) < 512:
            return
        extension = str(extension or "m4a").lower().lstrip(".")
        if extension not in _SAFE_EXTENSIONS:
            extension = "m4a"

        async with self._put_lock:
            await asyncio.to_thread(self._put_sync, bvid, data, extension)

    def _put_sync(self, bvid: str, data: bytes, extension: str) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / f"{bvid}.{extension}"
            temporary = self.root / f"{bvid}.{extension}.part"
            for old in self.root.glob(f"{bvid}.*"):
                if old != target and old.is_file():
                    old.unlink(missing_ok=True)
            temporary.write_bytes(data)
            os.replace(temporary, target)
            self._enforce_size_sync()
        except OSError:
            return

    def _enforce_size_sync(self) -> None:
        try:
            files = [
                path
                for path in self.root.iterdir()
                if path.is_file() and path.suffix != ".part" and not path.is_symlink()
            ]
        except OSError:
            return

        total = 0
        stats: list[tuple[float, int, Path]] = []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            stats.append((stat.st_mtime, stat.st_size, path))

        if total <= self.max_bytes:
            return

        for _mtime, size, path in sorted(stats):
            if total <= self.max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
                total -= size
            except OSError:
                continue
