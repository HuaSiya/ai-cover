"""AI 翻唱候选筛选与打分。

目标不是“搜到什么发什么”，而是尽量挑出：
1. 确实是这首歌的翻唱；
2. 标题/标签/简介里明确带 AI 翻唱特征；
3. 时长适合做成 QQ 语音；
4. 播放量、分区、完整度都还可以的版本。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

try:  # 插件运行时按包导入；tools/tests 里按顶层模块导入。
    from .bilibili import VideoSearchItem, clean_html_text
except ImportError:  # pragma: no cover - 仅独立运行测试时走到这里
    from bilibili import VideoSearchItem, clean_html_text


DEFAULT_QUERY_TEMPLATES = ("{song} AI翻唱", "{song} AI cover", "{song} 翻唱")

# 自然语言分支的负向前瞻：避开问句、评价、功能询问。
_NATURAL_META_NEGATIVE = (
    r"(?!功能|怎么|如何|什么|为什么|能不能|会不会|可以|吗|呢|吧|呗|教程|技巧|插件|活动|比赛|排行|推荐|歌名|的|过|得|了|是|很好听|好听|不错|厉害|棒|绝了|爱了|真好听|真不错|真棒|太棒|太好听"
    r"|歌(?:功能|怎么|如何|什么|为什么|能不能|会不会|可以|吗|呢|吧|呗|教程|技巧|插件|活动|比赛|排行|推荐|歌名|的|过|得|了|是|很好听|好听|不错|厉害|棒|绝了|爱了|真好听|真不错|真棒|太棒|太好听))"
)

# 同时兼容：
#   1. “/翻唱 晴天”“/点歌 晴天”“@机器人 /翻唱 晴天”
#   2. “@机器人 翻唱晴天”“@机器人 帮我翻唱一下《晴天》”
#   3. “@机器人 唱晴天”“@机器人 唱寒枝摇碎晚霞”“@机器人 唱首歌”
#   4. “@机器人 点歌晴天”“@机器人 帮我点歌一下《晴天》”
# 自然语言分支只匹配带 @ 的消息，并用负向前瞻避开“你会翻唱吗”“唱歌功能怎么用”等问句。
COMMAND_PATTERN = (
    r"^\s*(?:"
    r"(?:@.+?\s*)?[/#](?i:ai)?(?P<command>翻唱|点歌)\s*(?P<song>.*)"
    r"|"
    r"@.+?翻唱\s*" + _NATURAL_META_NEGATIVE + r"(?P<natural>.*)"
    r"|"
    r"@.+?唱\s*" + _NATURAL_META_NEGATIVE + r"(?P<sing>.*)"
    r"|"
    r"@.+?点歌\s*"
    r"(?!功能|怎么|如何|什么|为什么|能不能|会不会|可以|吗|呢|吧|呗|教程|技巧|插件|活动|比赛|排行|推荐|歌名|台|单|榜|的|过|得|了|是|很好听|好听|不错|厉害|棒|绝了|爱了|真好听|真不错|真棒|太棒|太好听)"
    r"(?P<request>.*)"
    r")$"
)

_COVER_PHRASES = (
    "翻唱",
    "cover",
    "歌ってみた",
    "唱见",
    "翻唱版",
    "翻唱ver",
    "coverver",
)

_AI_PHRASES = (
    "ai翻唱",
    "aicover",
    "ai音色",
    "ai换声",
    "ai声线",
    "ai歌手",
    "ai翻调",
    "ai唱歌",
    "ai演唱",
    "ai合成",
    "ai人声",
    "ai克隆",
    "ai虚拟",
    "ai版",
    "aivoice",
    "aisinger",
    "aiproduced",
    "sovits",
    "rvc",
    "seedvc",
    "ddsp",
    "diffsvc",
    "voiceclone",
    "声音克隆",
    "ai翻唱版",
    "a.i.",
)

# B 站的活动标签会被很多普通翻唱顺手带上，不能单独当作“这是 AI 翻唱”的证据。
_AI_EVENT_PHRASES = (
    "ai翻唱挑战赛",
    "ai翻唱挑战",
    "ai音乐征集大赛",
    "ai音乐征集",
    "ai音乐大赏",
    "ai音乐创作大赛",
    "ai翻唱大赛",
    "ai翻唱活动",
    "ai歌手大赛",
)

_AI_WORD_PATTERN = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])", re.IGNORECASE)
_WEAK_AI_PHRASES = (
    "音色",
    "克隆",
    "虚拟歌手",
    "声音合成",
    "ai音乐",
    "aimusic",
    "ai技术",
    "ai生成",
    "ai制作",
    "aigc",
    "aigenerated",
)

_HARD_NEGATIVE_TITLE = (
    "伴奏",
    "纯音乐",
    "instrumental",
    "offvocal",
    "off-vocal",
    "消音",
    "去人声",
    "和声伴奏",
    "教程",
    "教学",
    "翻弹",
    "指弹",
    "打谱",
    "扒谱",
    "midi",
    "reaction",
    "直播",
    "录播",
    "解说",
    "解析",
    "乐评",
    "合集",
    "串烧",
    "一小时",
    "1小时",
    "循环",
)

_SOFT_NEGATIVE_TITLE = (
    "钢琴",
    "吉他",
    "古筝",
    "二胡",
    "笛子",
    "萨克斯",
    "架子鼓",
    "清唱",
    "干声",
    "无伴奏",
    "纯人声",
    "预告",
    "片段",
    "试听",
    "混剪",
    "鬼畜",
    "搞笑",
    "恶搞",
    "翻车",
    "挑战",
)

_SOFT_NEGATIVE_COMBINED = (
    "教程",
    "教学",
    "伴奏",
    "纯音乐",
    "instrumental",
    "offvocal",
    "reaction",
    "直播",
    "录播",
    "解说",
)

_GENERIC_TOKENS = {"ai", "翻唱", "cover", "ai翻唱", "aicover", "翻唱版", "cover版"}

_TITLE_BRACKET_RE = re.compile(r"[《<「『]([^》>」』]{1,60})[》>」』]")

_COMMAND_RE = re.compile(COMMAND_PATTERN, re.DOTALL)
_AI_SUFFIX_RE = re.compile(
    r"\s*(?:[（(]?\s*(?:ai\s*翻唱|ai\s*cover|ai\s*音色)\s*[）)]?)\s*$",
    re.IGNORECASE,
)
_AI_PREFIX_RE = re.compile(r"^(?:ai\s*翻唱|ai\s*cover)\s*", re.IGNORECASE)


def extract_song(text: str, matched_groups: dict[str, object] | None = None) -> str:
    """从指令文本 / 正则命名组里取出歌名。"""

    song = ""
    natural = ""
    sing = ""
    request = ""
    if isinstance(matched_groups, dict):
        song = str(matched_groups.get("song") or "")
        natural = str(matched_groups.get("natural") or "")
        sing = str(matched_groups.get("sing") or "")
        request = str(matched_groups.get("request") or "")

    if not song and (natural or sing or request):
        song = _clean_natural_song(natural or sing or request)

    if not song:
        match = _COMMAND_RE.search(str(text or ""))
        if match is not None:
            matched = match.groupdict()
            song = str(matched.get("song") or "")
            if not song:
                song = _clean_natural_song(
                    str(
                        matched.get("natural")
                        or matched.get("sing")
                        or matched.get("request")
                        or ""
                    )
                )

    song = str(song or "").strip().strip("\"'“”‘’ ")
    song = re.sub(r"^[《<]+|[》>]+$", "", song).strip()
    song = _AI_SUFFIX_RE.sub("", song).strip()
    song = _AI_PREFIX_RE.sub("", song).strip()
    return song[:80].strip()


def extract_mode(text: str, matched_groups: dict[str, object] | None = None) -> str:
    """判断本次请求是“翻唱/唱歌”还是“点歌”。"""

    if isinstance(matched_groups, dict):
        command = str(matched_groups.get("command") or "")
        if command == "点歌" or matched_groups.get("request") is not None:
            return "request"
        if command == "翻唱" or matched_groups.get("natural") is not None or matched_groups.get("sing") is not None:
            return "cover"

    raw = str(text or "")
    if re.search(r"^[/#](?i:ai)?点歌", raw) or re.search(r"^@.+?点歌", raw):
        return "request"
    return "cover"


def normalize_text(value: str) -> str:
    """统一大小写、去掉标点空白和 HTML 标签，用于稳定匹配。"""

    text = clean_html_text(value).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


_GENERIC_SONG_VALUES = {
    "歌",
    "歌曲",
    "这首歌",
    "那首歌",
    "一首歌",
    "这个",
    "那个",
    "功能",
    "教程",
    "技巧",
    "插件",
    "活动",
    "比赛",
    "排行",
    "推荐",
    "歌名",
    "一下",
    "一首",
    "一个",
    "一遍",
}

_NATURAL_LEADING_FILLERS_RE = re.compile(
    r"^(?:(?:一下|一首|一个|一遍|首|个|版|呗|吧|呀|啊|啦)\s*)+",
    re.IGNORECASE,
)
_NATURAL_REPEAT_PREFIX_RE = re.compile(r"^(?:翻唱|唱)\s*", re.IGNORECASE)
_NATURAL_TRAILING_GENERIC_RE = re.compile(r"\s*(?:这首歌|那首歌|一首歌)\s*$")
_NATURAL_TRAILING_REQUEST_RE = re.compile(
    r"\s*(?:可以吗|好不好|行吗|好吗|可以嘛|谢谢(?:你)?|给我听|给你听|听听|很好听|不错|厉害|绝了|爱了|一下|呗|吧)\s*$",
    re.IGNORECASE,
)
_NATURAL_TRAILING_PARTICLES_RE = re.compile(r"\s*[吗呢呀啊啦]\s*$")
_NATURAL_AI_VERSION_RE = re.compile(
    r"[（(]\s*AI\s*(?:翻唱|cover|版)\s*[）)]\s*$",
    re.IGNORECASE,
)


def _clean_natural_song(value: str) -> str:
    """清理“帮我翻唱一下《晴天》可以吗”这类自然语言尾巴。"""

    text = str(value or "").strip()
    text = text.strip(" \t\r\n，,。.!！？?：:；;、-—~～")
    text = _NATURAL_LEADING_FILLERS_RE.sub("", text).strip()
    text = _NATURAL_REPEAT_PREFIX_RE.sub("", text).strip()
    text = _AI_PREFIX_RE.sub("", text).strip()
    text = _NATURAL_AI_VERSION_RE.sub("", text).strip()
    text = _NATURAL_TRAILING_GENERIC_RE.sub("", text).strip()
    text = _NATURAL_TRAILING_REQUEST_RE.sub("", text).strip()
    text = _NATURAL_TRAILING_PARTICLES_RE.sub("", text).strip()
    text = text.strip(" \t\r\n，,。.!！？?：:；;、-—~～")
    text = re.sub(r"^[《<「『\"'“”‘’]+|[》>」』\"'“”‘’]+$", "", text).strip()
    text = re.sub(
        r"\s*(?:的)?(?:AI翻唱版|翻唱版|完整版|版本)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    if normalize_text(text) in _GENERIC_SONG_VALUES:
        return ""
    return text[:80].strip()


def _strip_ai_event_phrases(normalized_text: str) -> str:
    """移除活动标签里的 AI 字样，避免普通翻唱被误判成 AI 翻唱。"""

    for phrase in _AI_EVENT_PHRASES:
        normalized_text = normalized_text.replace(phrase, " ")
    return normalized_text


# 这些词在标签里经常只是活动/技术话题，不足以单独证明“人声是 AI 的”。
_AMBIGUOUS_AI_TAGS = (
    "ai音乐",
    "aimusic",
    "ai技术",
    "ai生成",
    "ai制作",
    "aigc",
    "aigenerated",
)


def _strip_ambiguous_ai_tags(tags_text: str) -> str:
    for phrase in _AMBIGUOUS_AI_TAGS:
        tags_text = tags_text.replace(phrase, " ")
    return tags_text


def split_song_tokens(song: str) -> list[str]:
    """把 ``晴天 周杰伦`` 拆成可匹配的关键词。

    约定第一个词是歌名（README 也这样写），因此后续会强制要求第一个词命中。
    """

    raw_tokens = [part for part in re.split(r"[\s,，/、|&+\-—–]+", str(song or "")) if part.strip()]
    tokens: list[str] = []
    for raw in raw_tokens:
        token = normalize_text(raw)
        if not token:
            continue
        if token in _GENERIC_TOKENS:
            continue
        if len(token) == 1 and not token.isalnum():
            continue
        if token not in tokens:
            tokens.append(token)
    full = normalize_text(song)
    if not tokens and full:
        tokens.append(full)
    return tokens


@dataclass(frozen=True)
class Candidate:
    """一条过了硬过滤的候选。"""

    item: VideoSearchItem
    score: float
    is_ai: bool
    is_cover: bool
    reasons: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def bvid(self) -> str:
        return self.item.bvid


def evaluate_candidate(
    item: VideoSearchItem,
    song: str,
    *,
    require_ai: bool = True,
    min_duration: int = 20,
    max_duration: int = 600,
    min_score: float = 10.0,
) -> Candidate | None:
    """给单条搜索结果打分；不符合“这首歌的翻唱”时返回 ``None``。"""

    tokens = split_song_tokens(song)
    if not tokens:
        return None

    title_n = normalize_text(item.title)
    desc_n = normalize_text(item.description)
    tags_n = normalize_text(item.tags)
    typename_n = normalize_text(item.typename)
    combined = f"{title_n}|{desc_n}|{tags_n}"

    # ---------- 歌曲匹配 ----------
    main_token = tokens[0]

    # 避免把《半晴天》《像晴天像雨天》当成《晴天》：
    # 只要书名号里是另一首更长的歌名，就排除，不管标签/话题里是否重复出现“晴天”。
    bracket_contents = [normalize_text(content) for content in _TITLE_BRACKET_RE.findall(item.title)]
    if bracket_contents:
        exact_in_bracket = any(main_token == content for content in bracket_contents)
        inside_other_bracket = any(main_token in content and main_token != content for content in bracket_contents)
        if inside_other_bracket and not exact_in_bracket:
            return None

    in_title = main_token in title_n
    in_tags = main_token in tags_n
    in_desc = main_token in desc_n
    if not (in_title or in_tags or in_desc):
        return None

    # ---------- 翻唱判定 ----------
    title_cover = any(phrase in title_n for phrase in _COVER_PHRASES)
    any_cover = any(phrase in combined for phrase in _COVER_PHRASES) or title_cover
    typename_cover = "翻唱" in typename_n or "cover" in typename_n
    is_cover = any_cover or typename_cover
    if not is_cover:
        return None

    # ---------- AI 判定 ----------
    title_ai_text = _strip_ai_event_phrases(title_n)
    tags_ai_text = _strip_ambiguous_ai_tags(_strip_ai_event_phrases(tags_n))
    desc_ai_text = _strip_ai_event_phrases(desc_n)
    ai_text = f"{title_ai_text}|{tags_ai_text}|{desc_ai_text}"
    phrase_ai = any(phrase in ai_text for phrase in _AI_PHRASES)
    word_ai = bool(_AI_WORD_PATTERN.search(ai_text))
    typename_ai = "ai" in typename_n and ("音乐" in typename_n or "翻唱" in typename_n)
    is_ai = phrase_ai or word_ai or typename_ai
    weak_ai = any(phrase in f"{title_n}|{tags_n}|{desc_n}" for phrase in _WEAK_AI_PHRASES)

    if require_ai and not is_ai:
        return None

    # ---------- 硬过滤 ----------
    if title_n and any(phrase in title_n for phrase in _HARD_NEGATIVE_TITLE):
        return None
    if "原唱" in title_n and not is_cover:
        return None

    duration = int(item.duration_s or 0)
    if duration > 0 and (duration < max(1, min_duration) or duration > max(1, max_duration)):
        return None

    # ---------- 打分 ----------
    score = 0.0
    reasons: list[str] = []

    if in_title:
        score += 8.0
        reasons.append(f"标题含「{main_token}」")
    elif in_tags:
        score += 4.0
        reasons.append(f"标签含「{main_token}」")
    else:
        score += 2.0
        reasons.append(f"简介含「{main_token}」")

    for token in tokens[1:]:
        if token in title_n:
            score += 3.0
            reasons.append(f"标题含「{token}」")
        elif token in tags_n:
            score += 1.0

    full_song_n = normalize_text(song)
    if full_song_n and full_song_n in title_n:
        score += 3.0
        reasons.append("标题完整匹配")

    if title_cover:
        score += 5.0
        reasons.append("标题是翻唱")
    elif any_cover:
        score += 2.0
        reasons.append("简介/标签是翻唱")

    if typename_cover:
        score += 3.0
        reasons.append("B站翻唱分区")

    if is_ai:
        score += 9.0
        reasons.append("AI 翻唱特征")
    elif weak_ai:
        score += 1.0
        reasons.append("疑似 AI 音色")

    score += min(4.0, math.log10(max(item.play, 0) + 1))

    if 60 <= duration <= 360:
        score += 3.0
    elif 30 <= duration <= 600:
        score += 1.0
    elif duration > 0:
        score -= 3.0

    age_days = (time.time() - item.pubdate) / 86400 if item.pubdate > 0 else -1
    if 0 <= age_days <= 1095:
        score += 1.0

    for phrase in _SOFT_NEGATIVE_TITLE:
        if phrase in title_n:
            score -= 5.0
            reasons.append(f"标题含「{phrase}」")
    for phrase in _SOFT_NEGATIVE_COMBINED:
        if phrase in combined and phrase not in title_n:
            score -= 1.0

    if "原唱" in title_n and is_cover:
        score -= 2.0

    if score < float(min_score):
        return None

    return Candidate(
        item=item,
        score=round(score, 2),
        is_ai=is_ai,
        is_cover=is_cover,
        reasons=tuple(reasons),
    )


def rank_search_items(
    items: list[VideoSearchItem] | tuple[VideoSearchItem, ...],
    song: str,
    *,
    require_ai: bool = True,
    min_duration: int = 20,
    max_duration: int = 600,
    min_score: float = 10.0,
    limit: int = 0,
) -> list[Candidate]:
    """批量打分、去重并按分数降序返回。"""

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for item in items:
        if item.bvid in seen:
            continue
        seen.add(item.bvid)
        candidate = evaluate_candidate(
            item,
            song,
            require_ai=require_ai,
            min_duration=min_duration,
            max_duration=max_duration,
            min_score=min_score,
        )
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: (candidate.score, candidate.item.play, candidate.item.pubdate), reverse=True)
    if limit and limit > 0:
        return candidates[:limit]
    return candidates


# ===== 点歌模式：直接搜歌名，允许原唱/MV/翻唱，不要求 AI =====

_REQUEST_ALLOWED_TYPENAMES = {
    "音乐综合",
    "mv",
    "翻唱",
    "原创音乐",
    "日常",
    "音乐现场",
    "现场",
    "演唱会",
    "音乐",
    "歌曲",
    "音乐mv",
    "音乐视频",
}

_REQUEST_HARD_NEGATIVE_TITLE = (
    "伴奏",
    "纯音乐",
    "instrumental",
    "offvocal",
    "off-vocal",
    "消音",
    "去人声",
    "和声伴奏",
    "教程",
    "教学",
    "翻弹",
    "指弹",
    "打谱",
    "扒谱",
    "midi",
    "鼓谱",
    "简谱",
    "动态鼓谱",
    "钢琴简谱",
    "钢琴",
    "吉他",
    "古筝",
    "二胡",
    "笛子",
    "萨克斯",
    "架子鼓",
    "reaction",
    "直播",
    "录播",
    "解说",
    "解析",
    "乐评",
    "合集",
    "串烧",
    "一小时",
    "1小时",
    "循环",
    "舞蹈",
    "游戏",
    "影视",
    "动画",
    "鬼畜",
    "搞笑",
    "恶搞",
    "翻车",
    "挑战",
    "预告",
    "片段",
    "试听",
)

_REQUEST_TYPE_BONUS = {
    "音乐综合": 5.0,
    "mv": 4.0,
    "翻唱": 3.0,
    "原创音乐": 2.0,
    "音乐现场": 2.0,
    "现场": 2.0,
    "演唱会": 2.0,
    "音乐": 2.0,
    "歌曲": 2.0,
    "音乐mv": 2.0,
    "音乐视频": 2.0,
    "日常": 0.0,
}


def evaluate_request_candidate(
    item: VideoSearchItem,
    song: str,
    *,
    min_duration: int = 30,
    max_duration: int = 900,
    min_score: float = 8.0,
) -> Candidate | None:
    """给点歌候选打分：允许原唱/MV/翻唱/普通翻唱，只要歌名和时长合适。"""

    tokens = split_song_tokens(song)
    if not tokens:
        return None

    main_token = tokens[0]
    title_n = normalize_text(item.title)
    if main_token not in title_n:
        return None

    # 排除《半晴天》《像晴天像雨天》这类“书名号里是另一首歌”的情况。
    bracket_contents = [normalize_text(content) for content in _TITLE_BRACKET_RE.findall(item.title)]
    if bracket_contents:
        exact_in_bracket = any(main_token == content for content in bracket_contents)
        inside_other_bracket = any(main_token in content and main_token != content for content in bracket_contents)
        if inside_other_bracket and not exact_in_bracket:
            return None

    typename_n = normalize_text(item.typename)
    if typename_n and typename_n not in _REQUEST_ALLOWED_TYPENAMES:
        return None
    if any(phrase in title_n for phrase in _REQUEST_HARD_NEGATIVE_TITLE):
        return None

    duration = int(item.duration_s or 0)
    if duration > 0 and (duration < max(1, min_duration) or duration > max(1, max_duration)):
        return None

    score = 12.0
    reasons: list[str] = [f"标题含「{main_token}」"]

    full_song_n = normalize_text(song)
    if full_song_n and full_song_n in title_n:
        score += 3.0
        reasons.append("标题完整匹配")

    for token in tokens[1:]:
        if token in title_n:
            score += 2.0

    type_bonus = _REQUEST_TYPE_BONUS.get(typename_n, 0.0)
    score += type_bonus
    if type_bonus:
        reasons.append(f"分区「{item.typename}」")

    if any(marker in title_n for marker in ("原版", "官方", "无损", "hires", "4k")):
        score += 2.0
        reasons.append("原版/无损标记")

    combined = f"{title_n}|{normalize_text(item.description)}|{normalize_text(item.tags)}"
    is_cover = any(phrase in combined for phrase in _COVER_PHRASES) or "翻唱" in typename_n
    is_ai = any(phrase in combined for phrase in _AI_PHRASES) or bool(_AI_WORD_PATTERN.search(combined))
    if is_cover:
        score += 1.0
    if is_ai:
        score += 1.0

    score += min(5.0, math.log10(max(item.play, 0) + 1))

    if 60 <= duration <= 360:
        score += 3.0
    elif 30 <= duration <= 600:
        score += 1.0
    elif duration > 0:
        score -= 3.0

    age_days = (time.time() - item.pubdate) / 86400 if item.pubdate > 0 else -1
    if 0 <= age_days <= 1095:
        score += 0.5

    if score < float(min_score):
        return None

    return Candidate(
        item=item,
        score=round(score, 2),
        is_ai=is_ai,
        is_cover=is_cover,
        reasons=tuple(reasons),
    )


def rank_request_items(
    items: list[VideoSearchItem] | tuple[VideoSearchItem, ...],
    song: str,
    *,
    min_duration: int = 30,
    max_duration: int = 900,
    min_score: float = 8.0,
    limit: int = 0,
) -> list[Candidate]:
    """点歌模式批量打分、去重并按分数降序返回。"""

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for item in items:
        if item.bvid in seen:
            continue
        seen.add(item.bvid)
        candidate = evaluate_request_candidate(
            item,
            song,
            min_duration=min_duration,
            max_duration=max_duration,
            min_score=min_score,
        )
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: (candidate.score, candidate.item.play, candidate.item.pubdate), reverse=True)
    if limit and limit > 0:
        return candidates[:limit]
    return candidates


def expand_queries(song: str, templates: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """展开搜索关键词模板，去重并保持顺序。"""

    song = str(song or "").strip()
    if not song:
        return []

    raw_templates = list(templates or DEFAULT_QUERY_TEMPLATES)
    queries: list[str] = []
    for template in raw_templates:
        template = str(template or "").strip()
        if not template:
            continue
        try:
            query = template.format(song=song)
        except (KeyError, IndexError, ValueError):
            query = f"{song} {template}"
        query = " ".join(query.split())
        if query and query not in queries:
            queries.append(query)

    if not queries:
        queries = [f"{song} AI翻唱"]
    return queries[:6]


def format_duration(seconds: int) -> str:
    """秒数转 ``m:ss`` / ``h:mm:ss``。"""

    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


DEFAULT_CAPTION_TEMPLATE = "🎵 {title}\n👤 UP主：{author}\n⏱️ 时长：{duration}\n🔗 {url}"


def format_caption(
    template: str,
    *,
    title: str,
    author: str,
    duration: int,
    url: str,
    song: str,
    note: str = "",
) -> str:
    """渲染文案；模板写错时回退到默认模板，避免整条消息发不出去。"""

    values = {
        "title": title or song,
        "author": author or "未知 UP 主",
        "duration": format_duration(duration),
        "url": url,
        "song": song,
    }
    try:
        caption = str(template or "").format(**values)
    except (KeyError, IndexError, ValueError):
        caption = DEFAULT_CAPTION_TEMPLATE.format(**values)
    caption = caption.strip()
    if note:
        caption = f"{caption}\n{note}" if caption else note
    return caption[:1500]
