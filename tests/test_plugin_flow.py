"""用假宿主验证 /翻唱 指令完整流程（不联网、不发真实 QQ 消息）。"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import logging
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_PACKAGE = "ai_cover_flow_test"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        PLUGIN_PACKAGE,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PLUGIN_PACKAGE] = module
    spec.loader.exec_module(module)
    return module


class FakeSend:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.customs: list[tuple[str, object, str, dict]] = []

    async def text(self, text: str, stream_id: str, **kwargs) -> bool:
        del kwargs
        self.texts.append((str(text), str(stream_id)))
        return True

    async def custom(self, custom_type: str, data, stream_id: str, **kwargs) -> bool:
        self.customs.append((custom_type, data, stream_id, kwargs))
        return True


class FakeContext:
    def __init__(self, data_dir: Path) -> None:
        self.logger = logging.getLogger("ai_cover_flow_test")
        self.paths = SimpleNamespace(data_dir=data_dir)
        self.send = FakeSend()


class FakeBilibiliClient:
    def __init__(self, items, *, fail_bvids=()) -> None:
        self.items = list(items)
        self.fail_bvids = set(fail_bvids)

    async def search_videos(self, keyword: str, *, pages: int = 1):
        del keyword, pages
        return list(self.items)

    async def video_info(self, bvid: str):
        from ai_cover_flow_test.bilibili import VideoInfo

        item = next((item for item in self.items if item.bvid == bvid), None)
        title = item.title if item is not None else "测试视频"
        duration = item.duration_s if item is not None else 268
        return VideoInfo(
            bvid=bvid,
            aid=1,
            cid=12345,
            title=title,
            description="",
            duration_s=duration,
            owner="测试UP",
            view=1000,
            like=100,
            pubdate=0,
        )

    async def audio_streams(self, bvid: str, cid: int):
        del bvid, cid
        from ai_cover_flow_test.bilibili import AudioStream

        return [AudioStream(30216, 66_000, "audio/mp4", "mp4a.40.2", "https://example.com/audio.m4s")]

    def pick_stream(self, streams, quality: str = "low", *, duration_s: int = 0, max_bytes: int = 0):
        del quality, duration_s, max_bytes
        return streams[0] if streams else None

    async def download_audio(self, stream, *, max_bytes: int):
        del stream, max_bytes
        from ai_cover_flow_test.bilibili import AudioPayload

        if self.items:
            # 用当前选择的 bvid 判断失败；由 _try_candidate 传下来的流里没有 bvid，
            # 所以这里用调用顺序模拟：第一个 BV 失败时由 fail_bvids 控制。
            pass
        return AudioPayload(
            data=b"\x00\x00\x00\x18ftypmp42" + b"x" * 2048,
            content_type="video/mp4",
            extension="m4a",
            stream_id=30216,
        )

    async def close(self) -> None:
        return None


class FailingBilibiliClient(FakeBilibiliClient):
    """按 BV 号让指定候选的音频下载失败。"""

    def __init__(self, items, fail_bvids) -> None:
        super().__init__(items)
        self.fail_bvids = set(fail_bvids)
        self._current_bvid = ""

    async def download_audio(self, stream, *, max_bytes: int):
        del stream, max_bytes
        from ai_cover_flow_test.bilibili import BilibiliError

        if self._current_bvid in self.fail_bvids:
            raise BilibiliError("模拟下载失败")
        from ai_cover_flow_test.bilibili import AudioPayload

        return AudioPayload(
            data=b"\x00\x00\x00\x18ftypmp42" + b"x" * 2048,
            content_type="video/mp4",
            extension="m4a",
            stream_id=30216,
        )

    def pick_stream(self, streams, quality: str = "low", *, duration_s: int = 0, max_bytes: int = 0):
        # 从调用栈拿不到 bvid，改为在 audio_streams 里记录。
        return super().pick_stream(streams, quality, duration_s=duration_s, max_bytes=max_bytes)

    async def audio_streams(self, bvid: str, cid: int):
        self._current_bvid = bvid
        return await super().audio_streams(bvid, cid)


def make_item(
    module,
    *,
    bvid="BV1ZJ4A6gEkf",
    title="DS大肥鱼翻唱《晴天》 【AI翻唱】",
    play=563,
    tags="晴天,AI翻唱,翻唱",
    description="原曲：周杰伦",
    typename="翻唱",
):
    from ai_cover_flow_test.bilibili import VideoSearchItem

    return VideoSearchItem(
        bvid=bvid,
        title=title,
        author="雪碧极度酷冷",
        duration_s=268,
        play=play,
        description=description,
        tags=tags,
        typename=typename,
        pubdate=1793635613,
    )


class PluginFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.module = load_plugin_module()
        self.plugin = self.module.create_plugin()
        self.tempdir = tempfile.TemporaryDirectory()
        self.plugin._ctx = FakeContext(Path(self.tempdir.name))
        self.plugin.set_plugin_config(self.plugin.build_default_config())

    async def asyncTearDown(self) -> None:
        await self.plugin._close_services()
        self.tempdir.cleanup()

    async def invoke(self, text="/翻唱 晴天", song="晴天", group_id="123", user_id="456"):
        return await self.plugin.handle_cover(
            stream_id="stream-1",
            text=text,
            matched_groups={"song": song},
            group_id=group_id,
            user_id=user_id,
            message={},
        )

    async def test_sends_voice_only_by_default(self) -> None:
        self.plugin._client = FakeBilibiliClient([make_item(self.module)])
        success, _, _ = await self.invoke()

        self.assertTrue(success)
        send = self.plugin.ctx.send
        self.assertEqual(len(send.customs), 1)
        custom_type, data, stream_id, _ = send.customs[0]
        self.assertEqual(custom_type, "voice")
        self.assertEqual(stream_id, "stream-1")
        self.assertTrue(base64.b64decode(data).startswith(b"\x00\x00\x00\x18ftyp"))
        # 默认不发送等待提示、标题、UP 主或链接。
        self.assertEqual(send.texts, [])

    async def test_at_mention_command_sends_voice(self) -> None:
        from ai_cover_flow_test.covers import COMMAND_PATTERN

        self.plugin._client = FakeBilibiliClient([make_item(self.module)])
        text = "@No.32海咬龙.鲨龙 /翻唱 晴天"
        match = re.search(COMMAND_PATTERN, text)
        self.assertIsNotNone(match)
        assert match is not None

        success, _, _ = await self.plugin.handle_cover(
            stream_id="stream-1",
            text=text,
            matched_groups=match.groupdict(),
            group_id="123",
            user_id="456",
            message={},
        )
        self.assertTrue(success)
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        self.assertEqual(self.plugin.ctx.send.texts, [])

    async def test_natural_language_at_mention_sends_voice(self) -> None:
        from ai_cover_flow_test.covers import COMMAND_PATTERN

        self.plugin._client = FakeBilibiliClient([make_item(self.module)])
        text = "@bot 帮我翻唱一下《晴天》可以吗"
        match = re.search(COMMAND_PATTERN, text)
        self.assertIsNotNone(match)
        assert match is not None

        success, _, _ = await self.plugin.handle_cover(
            stream_id="stream-1",
            text=text,
            matched_groups=match.groupdict(),
            group_id="123",
            user_id="456",
            message={},
        )
        self.assertTrue(success)
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        self.assertEqual(self.plugin.ctx.send.texts, [])

    async def test_command_pattern_metadata_supports_at_mention(self) -> None:
        info = getattr(self.module.AiCoverPlugin.handle_cover, "__maibot_component_info__", None)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertIsNotNone(re.search(info.command_pattern, "@bot /翻唱 晴天"))
        self.assertIsNotNone(re.search(info.command_pattern, "/翻唱 晴天"))

    async def test_can_enable_ack_and_caption(self) -> None:
        config = self.plugin.build_default_config()
        config["delivery"]["send_ack"] = True
        config["delivery"]["send_caption"] = True
        self.plugin.set_plugin_config(config)
        self.plugin._client = FakeBilibiliClient([make_item(self.module)])

        success, _, _ = await self.invoke()
        self.assertTrue(success)
        joined = "\n".join(text for text, _ in self.plugin.ctx.send.texts)
        self.assertIn("正在找", joined)
        self.assertIn("DS大肥鱼", joined)
        self.assertIn("https://www.bilibili.com/video/BV1ZJ4A6gEkf", joined)

    async def test_no_result(self) -> None:
        self.plugin._client = FakeBilibiliClient([])
        success, _, _ = await self.invoke()
        self.assertFalse(success)
        self.assertEqual(self.plugin.ctx.send.customs, [])
        self.assertTrue(any("没有找到" in text for text, _ in self.plugin.ctx.send.texts))

    async def test_non_ai_fallback_is_silent(self) -> None:
        normal = make_item(
            self.module,
            title="【满穗翻唱】《晴天》",
            tags="晴天,翻唱,周杰伦",
            description="原曲：周杰伦",
            typename="翻唱",
        )
        self.plugin._client = FakeBilibiliClient([normal])
        success, _, _ = await self.invoke()
        self.assertTrue(success)
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        self.assertEqual(self.plugin.ctx.send.texts, [])

    async def test_cooldown(self) -> None:
        self.plugin._client = FakeBilibiliClient([make_item(self.module)])
        await self.plugin._ensure_services()
        await self.invoke()
        await self.invoke()
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        self.assertTrue(any("先歇" in text for text, _ in self.plugin.ctx.send.texts))

    async def test_daily_limit(self) -> None:
        config = self.plugin.build_default_config()
        config["limits"]["cooldown_seconds"] = 0
        config["limits"]["daily_limit"] = 1
        self.plugin.set_plugin_config(config)
        self.plugin._client = FakeBilibiliClient([make_item(self.module)])

        await self.invoke()
        await self.invoke()
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        self.assertTrue(any("今天已经点过" in text for text, _ in self.plugin.ctx.send.texts))

    async def test_disabled(self) -> None:
        config = self.plugin.build_default_config()
        config["plugin"]["enabled"] = False
        self.plugin.set_plugin_config(config)
        self.plugin._client = FakeBilibiliClient([make_item(self.module)])

        success, _, _ = await self.invoke()
        self.assertFalse(success)
        self.assertEqual(self.plugin.ctx.send.customs, [])
        self.assertEqual(self.plugin.ctx.send.texts, [])

    async def test_failed_audio_tries_next_candidate(self) -> None:
        config = self.plugin.build_default_config()
        config["delivery"]["send_caption"] = True
        self.plugin.set_plugin_config(config)
        first = make_item(self.module, bvid="BV1ZJ4A6gEkf", play=1000)
        second = make_item(
            self.module,
            bvid="BV1aVS9YqE1U",
            title="【AI翻唱】晴天 第二个版本",
            play=100,
        )
        client = FailingBilibiliClient([first, second], fail_bvids={"BV1ZJ4A6gEkf"})
        self.plugin._client = client

        success, _, _ = await self.invoke()
        self.assertTrue(success)
        self.assertEqual(len(self.plugin.ctx.send.customs), 1)
        # 第一个候选下载失败，不应发送它的文案
        captions = [text for text, _ in self.plugin.ctx.send.texts if "BV1ZJ4A6gEkf" in text]
        self.assertEqual(captions, [])
        self.assertTrue(any("BV1aVS9YqE1U" in text for text, _ in self.plugin.ctx.send.texts))


if __name__ == "__main__":
    unittest.main()
