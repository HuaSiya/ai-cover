"""B 站响应解析与音流选择测试（不联网）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili import (  # noqa: E402
    AudioStream,
    BilibiliClient,
    _guess_extension,
    _parse_search_item,
)


class SearchItemParsingTests(unittest.TestCase):
    def test_parse_search_item(self) -> None:
        raw = {
            "bvid": "BV1ZJ4A6gEkf",
            "title": 'DS大肥鱼<em class="keyword">翻唱</em>《<em class="keyword">晴天</em>》  【<em class="keyword">AI翻唱</em>】',
            "author": "雪碧极度酷冷",
            "duration": "4:28",
            "play": 563,
            "description": "原曲：周杰伦",
            "tag": "晴天,AI翻唱,翻唱",
            "typename": "翻唱",
            "pubdate": 1793635613,
            "aid": 117547684862680,
            "pic": "//i0.hdslb.com/bfs/archive/xxx.jpg",
        }
        item = _parse_search_item(raw)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.bvid, "BV1ZJ4A6gEkf")
        self.assertEqual(item.title, "DS大肥鱼翻唱《晴天》 【AI翻唱】")
        self.assertEqual(item.duration_s, 268)
        self.assertEqual(item.play, 563)
        self.assertEqual(item.typename, "翻唱")
        self.assertEqual(item.cover, "https://i0.hdslb.com/bfs/archive/xxx.jpg")
        self.assertEqual(item.url, "https://www.bilibili.com/video/BV1ZJ4A6gEkf")

    def test_parse_search_item_from_arcurl(self) -> None:
        raw = {
            "arcurl": "http://www.bilibili.com/video/BV1ZJ4A6gEkf",
            "title": "晴天 AI翻唱",
            "duration": "4:28",
        }
        item = _parse_search_item(raw)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.bvid, "BV1ZJ4A6gEkf")

    def test_parse_search_item_rejects_invalid(self) -> None:
        self.assertIsNone(_parse_search_item({"title": "没有 BV 号"}))
        self.assertIsNone(_parse_search_item({"bvid": "not-a-bv", "title": "x"}))


class StreamSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.streams = [
            AudioStream(30216, 66_000, "audio/mp4", "mp4a.40.2", "https://a/64"),
            AudioStream(30232, 132_000, "audio/mp4", "mp4a.40.2", "https://a/132"),
            AudioStream(30280, 192_000, "audio/mp4", "mp4a.40.2", "https://a/192"),
        ]

    def test_quality_preferences(self) -> None:
        self.assertEqual(BilibiliClient.pick_stream(self.streams, "low").stream_id, 30216)
        self.assertEqual(BilibiliClient.pick_stream(self.streams, "medium").stream_id, 30232)
        self.assertEqual(BilibiliClient.pick_stream(self.streams, "high").stream_id, 30280)

    def test_size_filter(self) -> None:
        # 192k * 600s / 8 = 14.4 MB，超过 9 MB，应被过滤
        stream = BilibiliClient.pick_stream(
            self.streams,
            "high",
            duration_s=600,
            max_bytes=9 * 1024 * 1024,
        )
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertNotEqual(stream.stream_id, 30280)

    def test_all_too_large_returns_none(self) -> None:
        stream = BilibiliClient.pick_stream(
            self.streams,
            "low",
            duration_s=10_000,
            max_bytes=1024 * 1024,
        )
        self.assertIsNone(stream)

    def test_durl_video_fallback(self) -> None:
        durl = [AudioStream(0, 0, "video/mp4", "", "https://a/video", (), 10, True)]
        self.assertEqual(BilibiliClient.pick_stream(durl, "low").is_video, True)

    def test_guess_extension(self) -> None:
        self.assertEqual(_guess_extension(b"ID3....", "audio/mpeg", self.streams[0]), "mp3")
        self.assertEqual(_guess_extension(b"\x00\x00\x00\x18ftypmp42", "video/mp4", self.streams[0]), "m4a")
        self.assertEqual(_guess_extension(b"OggS....", "audio/ogg", self.streams[0]), "ogg")


if __name__ == "__main__":
    unittest.main()
