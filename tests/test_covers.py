"""候选择优与文本解析的单元测试（不联网）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili import (  # noqa: E402
    VideoSearchItem,
    clean_html_text,
    parse_count,
    parse_duration,
)
from covers import (  # noqa: E402
    COMMAND_PATTERN,
    evaluate_candidate,
    evaluate_request_candidate,
    expand_queries,
    extract_mode,
    extract_song,
    format_caption,
    format_duration,
    rank_request_items,
    rank_search_items,
    split_song_tokens,
)


def make_item(**overrides) -> VideoSearchItem:
    values = {
        "bvid": "BV1ZJ4A6gEkf",
        "title": "DS大肥鱼翻唱《晴天》  【AI翻唱】",
        "author": "雪碧极度酷冷",
        "duration_s": 268,
        "play": 563,
        "description": "音色：AI 大肥鱼；原曲：周杰伦",
        "tags": "晴天,AI翻唱,翻唱,大肥鱼",
        "typename": "翻唱",
        "pubdate": 1793635613,
        "aid": 117547684862680,
    }
    values.update(overrides)
    return VideoSearchItem(**values)


class TextParsingTests(unittest.TestCase):
    def test_parse_duration(self) -> None:
        self.assertEqual(parse_duration("4:28"), 268)
        self.assertEqual(parse_duration("1:02:03"), 3723)
        self.assertEqual(parse_duration("268"), 268)
        self.assertEqual(parse_duration(268), 268)
        self.assertEqual(parse_duration(""), 0)
        self.assertEqual(parse_duration("abc"), 0)

    def test_parse_count(self) -> None:
        self.assertEqual(parse_count("563"), 563)
        self.assertEqual(parse_count("1.2万"), 12000)
        self.assertEqual(parse_count("3.4亿"), 340000000)
        self.assertEqual(parse_count("1.2w"), 12000)
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count(None), 0)

    def test_clean_html_text(self) -> None:
        self.assertEqual(clean_html_text('<em class="keyword">晴天</em> AI翻唱'), "晴天 AI翻唱")
        self.assertEqual(clean_html_text("A &amp; B"), "A & B")
        self.assertEqual(clean_html_text("  多   空格  "), "多 空格")

    def test_split_song_tokens(self) -> None:
        self.assertEqual(split_song_tokens("晴天"), ["晴天"])
        self.assertEqual(split_song_tokens("晴天 周杰伦"), ["晴天", "周杰伦"])
        self.assertEqual(split_song_tokens("晴天-周杰伦"), ["晴天", "周杰伦"])
        self.assertEqual(split_song_tokens("晴天 / 周杰伦"), ["晴天", "周杰伦"])
        self.assertEqual(split_song_tokens("AI翻唱 晴天"), ["晴天"])
        self.assertEqual(split_song_tokens("Lemon"), ["lemon"])

    def test_extract_song(self) -> None:
        self.assertEqual(extract_song("/翻唱 晴天", None), "晴天")
        self.assertEqual(extract_song("/翻唱《晴天》", {}), "晴天")
        self.assertEqual(extract_song("#翻唱 晴天 周杰伦", None), "晴天 周杰伦")
        self.assertEqual(extract_song("/ai翻唱 起风了", None), "起风了")
        self.assertEqual(extract_song("/翻唱 晴天 AI翻唱", None), "晴天")
        self.assertEqual(extract_song("/翻唱", None), "")

    def test_extract_song_with_at_mention(self) -> None:
        self.assertEqual(extract_song("@No.32海咬龙.鲨龙 /翻唱 晴天", None), "晴天")
        self.assertEqual(extract_song("@bot /翻唱 晴天", {}), "晴天")
        self.assertEqual(extract_song("@bot /翻唱 晴天 周杰伦", None), "晴天 周杰伦")
        self.assertEqual(extract_song("@bot #翻唱 起风了", None), "起风了")

    def test_extract_song_with_request_command(self) -> None:
        self.assertEqual(extract_song("/点歌 晴天", None), "晴天")
        self.assertEqual(extract_song("@bot 点歌 晴天", None), "晴天")
        self.assertEqual(extract_song("@bot 帮我点歌一下《晴天》", None), "晴天")
        self.assertEqual(extract_song("@bot 点歌一首晴天", None), "晴天")
        self.assertEqual(extract_song("@bot 点歌这首歌", None), "")
        self.assertEqual(extract_song("@bot 点歌功能怎么用", None), "")

    def test_extract_mode(self) -> None:
        self.assertEqual(extract_mode("/点歌 晴天", None), "request")
        self.assertEqual(extract_mode("@bot 点歌 晴天", None), "request")
        self.assertEqual(extract_mode("@bot 点歌", None), "request")
        self.assertEqual(extract_mode("/翻唱 晴天", None), "cover")
        self.assertEqual(extract_mode("@bot 翻唱晴天", None), "cover")
        self.assertEqual(extract_mode("@bot 唱晴天", None), "cover")
        self.assertEqual(extract_mode("@bot 唱首歌", None), "cover")

    def test_extract_natural_language_song(self) -> None:
        self.assertEqual(extract_song("@bot 翻唱晴天", None), "晴天")
        self.assertEqual(extract_song("@bot 帮我翻唱一下《晴天》", None), "晴天")
        self.assertEqual(extract_song("@bot 能翻唱晴天吗", None), "晴天")
        self.assertEqual(extract_song("@bot 翻唱《起风了》这首歌", None), "起风了")
        self.assertEqual(extract_song("@bot 来一首翻唱：晴天 周杰伦", None), "晴天 周杰伦")
        self.assertEqual(extract_song("@bot 翻唱晴天给我听", None), "晴天")
        self.assertEqual(extract_song("@bot 翻唱晴天（AI版）", None), "晴天")
        self.assertEqual(extract_song("@bot 唱寒枝摇碎晚霞", None), "寒枝摇碎晚霞")
        self.assertEqual(extract_song("@bot 唱一下《晴天》", None), "晴天")
        self.assertEqual(extract_song("@bot 给我唱一首晴天", None), "晴天")
        self.assertEqual(extract_song("@bot 能唱《起风了》吗", None), "起风了")
        self.assertEqual(extract_song("@bot 唱首歌", None), "")
        self.assertEqual(extract_song("@bot 唱这首歌", None), "")
        self.assertEqual(extract_song("@bot 唱得不错", None), "")
        self.assertEqual(extract_song("@bot 唱歌功能怎么用", None), "")
        self.assertEqual(extract_song("@bot 翻唱一下这首歌", None), "")
        self.assertEqual(extract_song("@bot 你会翻唱吗", None), "")
        self.assertEqual(extract_song("@bot 翻唱功能怎么用", None), "")

    def test_command_pattern_matches_at_mention(self) -> None:
        import re

        match = re.search(COMMAND_PATTERN, "@No.32海咬龙.鲨龙 /翻唱 寒枝揺碎晚霞")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("song"), "寒枝揺碎晚霞")

        match = re.search(COMMAND_PATTERN, "/翻唱 晴天")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("song"), "晴天")

        match = re.search(COMMAND_PATTERN, " @bot /ai翻唱 Lemon")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("song"), "Lemon")

    def test_command_pattern_matches_natural_language(self) -> None:
        import re

        for text, expected in (
            ("@bot 翻唱晴天", "晴天"),
            ("@bot 帮我翻唱一下《晴天》可以吗", "晴天"),
            ("@bot 能翻唱《起风了》吗", "起风了"),
            ("@bot 来一首翻唱：Lemon", "Lemon"),
            ("@bot 唱寒枝摇碎晚霞", "寒枝摇碎晚霞"),
            ("@bot 唱一下《晴天》可以吗", "晴天"),
            ("@bot 给我唱一首晴天", "晴天"),
        ):
            match = re.search(COMMAND_PATTERN, text)
            self.assertIsNotNone(match, text)
            assert match is not None
            self.assertEqual(extract_song(text, match.groupdict()), expected)

        for text in (
            "@bot 你会翻唱吗",
            "@bot 翻唱功能怎么用",
            "@bot 你翻唱的晴天很好听",
            "@bot 唱得不错",
            "@bot 唱歌功能怎么用",
            "@bot 唱得真好",
        ):
            match = re.search(COMMAND_PATTERN, text)
            self.assertIsNone(match, text)

        for text in (
            "@bot 翻唱这首歌",
            "@bot 唱这首歌",
            "@bot 唱首歌",
        ):
            match = re.search(COMMAND_PATTERN, text)
            self.assertIsNotNone(match, text)
            assert match is not None
            self.assertEqual(extract_song(text, match.groupdict()), "")

    def test_command_pattern_matches_song_request(self) -> None:
        import re

        for text in (
            "/点歌 晴天",
            "@bot 点歌 晴天",
            "@bot 帮我点歌一下《晴天》",
        ):
            match = re.search(COMMAND_PATTERN, text)
            self.assertIsNotNone(match, text)
            assert match is not None
            self.assertEqual(extract_mode(text, match.groupdict()), "request")
            self.assertEqual(extract_song(text, match.groupdict()), "晴天")

        for text in (
            "@bot 点歌功能怎么用",
            "@bot 点歌台",
            "@bot 点歌榜",
        ):
            self.assertIsNone(re.search(COMMAND_PATTERN, text), text)

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(268), "4:28")
        self.assertEqual(format_duration(3723), "1:02:03")
        self.assertEqual(format_duration(0), "0:00")

    def test_format_caption_fallback(self) -> None:
        caption = format_caption(
            "坏模板 {unknown}",
            title="标题",
            author="UP",
            duration=268,
            url="https://example.com",
            song="晴天",
        )
        self.assertIn("标题", caption)
        self.assertIn("4:28", caption)
        self.assertIn("https://example.com", caption)

    def test_expand_queries(self) -> None:
        queries = expand_queries("晴天", ["{song} AI翻唱", "{song} AI翻唱", "{song} 翻唱"])
        self.assertEqual(queries, ["晴天 AI翻唱", "晴天 翻唱"])


class CandidateTests(unittest.TestCase):
    def test_ai_cover_accepted(self) -> None:
        candidate = evaluate_candidate(make_item(), "晴天")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.is_ai)
        self.assertTrue(candidate.is_cover)
        self.assertGreater(candidate.score, 20)

    def test_normal_cover_requires_fallback(self) -> None:
        item = make_item(
            bvid="BV1aVS9YqE1U",
            title="【满穗翻唱】从前从前有个人爱你很久《晴天》",
            description="原曲：周杰伦",
            tags="晴天,翻唱,周杰伦",
            play=3859,
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))
        candidate = evaluate_candidate(item, "晴天", require_ai=False)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.is_ai)
        self.assertTrue(candidate.is_cover)

    def test_tutorial_rejected(self) -> None:
        item = make_item(title="《晴天》吉他弹唱教学", tags="晴天,吉他,教学", typename="音乐教学")
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=False))

    def test_instrumental_rejected(self) -> None:
        item = make_item(title="《晴天》纯音乐 钢琴版", tags="晴天,纯音乐,钢琴", typename="演奏")
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=False))

    def test_wrong_song_rejected(self) -> None:
        item = make_item(title="AI翻唱《七里香》", tags="七里香,AI翻唱")
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=False))

    def test_ai_original_without_cover_rejected(self) -> None:
        item = make_item(title="AI晴天 原创歌曲", tags="晴天,原创,AI音乐", typename="原创音乐")
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=False))

    def test_ai_event_tag_alone_is_not_ai(self) -> None:
        item = make_item(
            title="周杰伦《晴天》女声完整版",
            tags="晴天,完整版,周杰伦,AI翻唱挑战赛,空灵女声,治愈女声",
            description="#周杰伦晴天 #女声完整版 #翻唱cover",
            typename="翻唱",
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))
        candidate = evaluate_candidate(item, "晴天", require_ai=False)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.is_ai)

    def test_ai_music_tag_alone_is_not_ai(self) -> None:
        item = make_item(
            title="恢宏国风交响翻唱《晴天》",
            tags="晴天,翻唱,AI音乐,歌曲改编,伴奏",
            description="高质量伴奏，需要可私信联系",
            typename="翻唱",
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))
        candidate = evaluate_candidate(item, "晴天", require_ai=False)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.is_ai)

    def test_ai_music_category_is_ai(self) -> None:
        item = make_item(
            title="【翻唱】《晴天》",
            tags="晴天,翻唱",
            description="原曲：周杰伦",
            typename="AI音乐",
        )
        candidate = evaluate_candidate(item, "晴天", require_ai=True)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.is_ai)

    def test_rvc_description_counts_as_ai(self) -> None:
        item = make_item(
            title="【满穗翻唱】《晴天》",
            tags="晴天,翻唱,满穗",
            description="项目地址：https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI",
            typename="AI音乐",
        )
        candidate = evaluate_candidate(item, "晴天", require_ai=True)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.is_ai)

    def test_ai_cover_ranks_above_popular_normal_cover(self) -> None:
        ai_item = make_item(play=100)
        normal_item = make_item(
            bvid="BV1aVS9YqE1U",
            title="【满穗翻唱】《晴天》",
            description="原曲：周杰伦",
            tags="晴天,翻唱",
            play=1_000_000,
        )
        candidates = rank_search_items([normal_item, ai_item], "晴天", require_ai=False)
        self.assertEqual(candidates[0].bvid, ai_item.bvid)
        self.assertTrue(candidates[0].is_ai)

    def test_duration_filter(self) -> None:
        item = make_item(duration_s=1200)
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=False, max_duration=600))

    def test_other_song_containing_song_name_rejected(self) -> None:
        item = make_item(
            title="2000年黄砖时期的陶喆唱《半晴天》【Ai翻唱】",
            tags="半晴天,AI翻唱,陶喆",
            typename="翻唱",
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))

    def test_other_song_with_hashtag_occurrences_rejected(self) -> None:
        item = make_item(
            title="《像晴天像雨天》AI Cover 孙燕姿完整版#孙燕姿 #像晴天像雨天 #ai翻唱",
            tags="像晴天像雨天,AI翻唱,孙燕姿",
            typename="翻唱",
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))

    def test_other_song_with_longer_bracketed_name_rejected(self) -> None:
        item = make_item(
            title="《我的晴天》 AI翻唱",
            tags="我的晴天,AI翻唱",
            typename="翻唱",
        )
        self.assertIsNone(evaluate_candidate(item, "晴天", require_ai=True))

    def test_exact_bracketed_song_still_accepted(self) -> None:
        item = make_item(title="《晴天》| AI 翻唱", tags="晴天,AI翻唱,翻唱", typename="翻唱")
        candidate = evaluate_candidate(item, "晴天", require_ai=True)
        self.assertIsNotNone(candidate)


class RequestCandidateTests(unittest.TestCase):
    def test_original_song_audio_accepted(self) -> None:
        item = make_item(
            title="【Hi-Res无损音质】｜《晴天》- 周杰伦",
            tags="晴天,周杰伦",
            description="",
            typename="音乐综合",
            play=3_518_347,
        )
        candidate = evaluate_request_candidate(item, "晴天")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertGreater(candidate.score, 25)

    def test_tutorial_rejected(self) -> None:
        item = make_item(title="《晴天》原调教学", tags="晴天,教学", typename="音乐教学")
        self.assertIsNone(evaluate_request_candidate(item, "晴天"))

    def test_instrumental_rejected(self) -> None:
        item = make_item(title="晴天 钢琴简谱", tags="晴天,钢琴", typename="演奏")
        self.assertIsNone(evaluate_request_candidate(item, "晴天"))

    def test_wrong_song_rejected(self) -> None:
        item = make_item(title="《七里香》- 周杰伦", tags="七里香,周杰伦", typename="音乐综合")
        self.assertIsNone(evaluate_request_candidate(item, "晴天"))

    def test_other_bracketed_song_rejected(self) -> None:
        item = make_item(
            title="《像晴天像雨天》AI Cover 孙燕姿完整版",
            tags="像晴天像雨天,AI翻唱",
            typename="音乐综合",
        )
        self.assertIsNone(evaluate_request_candidate(item, "晴天"))

    def test_ranking_prefers_music_over_daily(self) -> None:
        music = make_item(
            bvid="BV1Music0001",
            title="【Hi-Res无损】《晴天》- 周杰伦",
            tags="晴天,周杰伦",
            typename="音乐综合",
            play=1000,
        )
        daily = make_item(
            bvid="BV1Daily0001",
            title="《晴天》日常翻唱",
            tags="晴天,翻唱",
            typename="日常",
            play=1_000_000,
        )
        candidates = rank_request_items([daily, music], "晴天")
        self.assertEqual(candidates[0].bvid, music.bvid)


if __name__ == "__main__":
    unittest.main()
