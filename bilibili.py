"""B 站只读客户端：搜索视频、读取信息、获取并下载音频。

这个模块刻意不依赖 MaiBot SDK，方便单独运行网络测试：

    python tools/check_bilibili.py "晴天"

B 站搜索接口对匿名请求有风控，必须先拿 ``buvid3/buvid4`` Cookie；
本客户端会自动处理 Cookie、限速、WBI 签名和多接口回退。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import httpx

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.bilibili.com"
_SEARCH_REFERER = "https://search.bilibili.com/"
_VIDEO_REFERER = "https://www.bilibili.com/"
_BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# B 站 WBI 签名用的 64 位重排表（前端常量，长期稳定）。
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BilibiliError(RuntimeError):
    """B 站接口或音频下载异常。"""

    def __init__(self, message: str, *, code: int | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def retryable(self) -> bool:
        """是否为值得换 Cookie / 重试的风险控制类错误。"""

        return self.status in {412, 429, 500, 502, 503, 504} or self.code in {-352, -412, -509}


@dataclass(frozen=True)
class VideoSearchItem:
    """搜索结果中的一条视频。"""

    bvid: str
    title: str
    author: str = ""
    duration_s: int = 0
    play: int = 0
    description: str = ""
    tags: str = ""
    typename: str = ""
    pubdate: int = 0
    aid: int = 0
    cover: str = ""

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


@dataclass(frozen=True)
class VideoInfo:
    """视频详情。"""

    bvid: str
    aid: int
    cid: int
    title: str
    description: str
    duration_s: int
    owner: str
    view: int
    like: int
    pubdate: int

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


@dataclass(frozen=True)
class AudioStream:
    """一条可下载的音频流。"""

    stream_id: int
    bandwidth: int
    mime_type: str
    codecs: str
    base_url: str
    backup_urls: tuple[str, ...] = field(default_factory=tuple)
    size: int = 0
    is_video: bool = False


@dataclass(frozen=True)
class AudioPayload:
    """下载完成的音频。"""

    data: bytes
    content_type: str
    extension: str
    stream_id: int


@dataclass
class BilibiliOptions:
    """客户端配置。"""

    sessdata: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    rate_limit_seconds: float = 1.0
    request_timeout_seconds: float = 15.0
    download_timeout_seconds: float = 60.0
    use_env_proxy: bool = False
    proxy_url: str = ""


# ---------------------------------------------------------------------------
# 纯文本解析工具（也供 covers.py 复用）
# ---------------------------------------------------------------------------


def clean_html_text(value: Any) -> str:
    """去掉 B 站搜索标题里的 ``<em>`` 等标签并还原 HTML 实体。"""

    text = html.unescape(str(value or ""))
    text = _HTML_TAG_PATTERN.sub("", text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def parse_duration(value: Any) -> int:
    """把 ``4:28`` / ``1:02:03`` / ``268`` 解析为秒数，失败返回 0。"""

    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)

    parts = re.split(r"[:：]", text)
    if len(parts) in {2, 3} and all(part.strip().isdigit() for part in parts):
        numbers = [int(part) for part in parts]
        if len(numbers) == 2:
            return max(0, numbers[0] * 60 + numbers[1])
        return max(0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])

    match = re.search(
        r"(?:(\d+)\s*(?:小时|时))?\s*(?:(\d+)\s*分(?:钟)?)?\s*(?:(\d+)\s*秒)?",
        text,
    )
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else 0


def parse_count(value: Any) -> int:
    """把 ``563`` / ``1.2万`` / ``3.4亿`` / ``1.2w`` 解析为整数。"""

    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value or "").strip().replace(",", "").replace("，", "")
    if not text:
        return 0

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([万亿wW]?)$", text)
    if match is None:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万亿wW]?)", text)
    if match is None:
        return 0

    number = float(match.group(1))
    multiplier = {"万": 10_000, "w": 10_000, "W": 10_000, "亿": 100_000_000}.get(match.group(2), 1)
    return max(0, int(number * multiplier))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_search_item(raw: dict[str, Any]) -> VideoSearchItem | None:
    """把搜索接口返回的原始条目转为强类型对象。"""

    bvid = str(raw.get("bvid") or "").strip()
    if not bvid:
        found = _BV_PATTERN.search(str(raw.get("arcurl") or raw.get("url") or ""))
        bvid = found.group(0) if found else ""
    if not _BV_PATTERN.fullmatch(bvid):
        return None

    title = clean_html_text(raw.get("title") or raw.get("name") or "")
    if not title:
        return None

    tags = raw.get("tag") or raw.get("tags") or ""
    if isinstance(tags, (list, tuple)):
        tags = ",".join(str(tag) for tag in tags)

    cover = str(raw.get("pic") or raw.get("cover") or "").strip()
    if cover.startswith("//"):
        cover = "https:" + cover

    return VideoSearchItem(
        bvid=bvid,
        title=title,
        author=clean_html_text(raw.get("author") or raw.get("uname") or ""),
        duration_s=parse_duration(raw.get("duration")),
        play=parse_count(raw.get("play") or raw.get("view") or 0),
        description=clean_html_text(raw.get("description") or raw.get("desc") or ""),
        tags=clean_html_text(tags),
        typename=clean_html_text(raw.get("typename") or raw.get("cate_name") or ""),
        pubdate=_to_int(raw.get("pubdate") or raw.get("senddate") or 0),
        aid=_to_int(raw.get("aid") or raw.get("id") or 0),
        cover=cover,
    )


def _iter_video_entries(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """遍历搜索响应里的视频条目，兼容 type=video 和 all/v2 两种结构。"""

    data = payload.get("data")
    if not isinstance(data, dict):
        return

    result = data.get("result")
    if isinstance(result, list):
        for entry in result:
            if not isinstance(entry, dict):
                continue
            if entry.get("result_type") == "video" and isinstance(entry.get("data"), list):
                for item in entry["data"]:
                    if isinstance(item, dict):
                        yield item
            elif entry.get("bvid"):
                yield entry


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


class BilibiliClient:
    """极简异步 B 站客户端。所有 API 请求串行限速，音频下载不占用 API 限速。"""

    def __init__(self, options: BilibiliOptions | None = None, *, client: httpx.AsyncClient | None = None) -> None:
        self.options = options or BilibiliOptions()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            trust_env=bool(self.options.use_env_proxy),
            proxy=self.options.proxy_url or None,
            timeout=httpx.Timeout(self.options.request_timeout_seconds),
        )
        self._cookie = ""
        self._cookie_at = 0.0
        self._cookie_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._mixin_key = ""
        self._mixin_at = 0.0
        self._mixin_lock = asyncio.Lock()

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""

        if self._owns_client:
            await self._client.aclose()

    # ---------------- 请求基础设施 ----------------

    def _base_headers(self, *, referer: str = _VIDEO_REFERER) -> dict[str, str]:
        headers = {
            "User-Agent": self.options.user_agent,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def _throttle(self) -> None:
        """API 请求之间保持最小间隔，避免触发 -352 风控。"""

        rate = max(0.0, float(self.options.rate_limit_seconds))
        async with self._rate_lock:
            wait = self._last_request_at + rate - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def _ensure_cookies(self, *, force: bool = False) -> str:
        """获取匿名指纹 Cookie；没有它搜索接口会直接返回 412。"""

        async with self._cookie_lock:
            if self._cookie and not force and (time.time() - self._cookie_at) < 6 * 3600:
                return self._cookie

            b3 = ""
            b4 = ""
            try:
                await self._throttle()
                response = await self._client.get(
                    f"{_API_ROOT}/x/frontend/finger/spi",
                    headers={"User-Agent": self.options.user_agent, "Referer": _VIDEO_REFERER},
                    timeout=self.options.request_timeout_seconds,
                )
                if response.status_code == 200:
                    payload = response.json()
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if isinstance(data, dict):
                        b3 = str(data.get("b_3") or "")
                        b4 = str(data.get("b_4") or "")
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("获取 B 站 buvid 失败，使用随机指纹: %s", exc)

            if not b3:
                b3 = f"{uuid.uuid4()!s}".upper() + "infoc"
            if not b4:
                b4 = f"{uuid.uuid4()!s}".upper()

            cookie_parts = [
                f"buvid3={b3}",
                f"buvid4={b4}",
                f"b_nut={int(time.time())}",
            ]
            sessdata = str(self.options.sessdata or "").strip()
            if sessdata:
                if sessdata.upper().startswith("SESSDATA="):
                    sessdata = sessdata.split("=", 1)[1]
                cookie_parts.append(f"SESSDATA={sessdata}")

            self._cookie = "; ".join(cookie_parts)
            self._cookie_at = time.time()
            return self._cookie

    async def _request_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        referer: str = _VIDEO_REFERER,
        retry: int = 2,
        allowed_codes: set[int] | None = None,
    ) -> dict[str, Any]:
        """请求 B 站 JSON 接口，自动处理 Cookie 刷新与风险控制重试。"""

        allowed = allowed_codes or {0}
        url = f"{_API_ROOT}{path}"
        last_error: Exception | None = None

        for attempt in range(max(0, retry) + 1):
            await self._ensure_cookies(force=attempt > 0)
            await self._throttle()
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=self._base_headers(referer=referer),
                    timeout=self.options.request_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = BilibiliError(f"请求 B 站失败: {exc}")
                if attempt < retry:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise last_error from exc

            if response.status_code in {412, 429} or response.status_code >= 500:
                last_error = BilibiliError(
                    f"B 站返回 HTTP {response.status_code}",
                    status=response.status_code,
                )
                if attempt < retry:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                raise last_error

            try:
                payload = response.json()
            except ValueError as exc:
                last_error = BilibiliError(f"B 站返回非 JSON（HTTP {response.status_code}）", status=response.status_code)
                if attempt < retry:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise last_error from exc

            if not isinstance(payload, dict):
                raise BilibiliError("B 站响应结构异常")

            code = _to_int(payload.get("code"), -1)
            data = payload.get("data")
            voucher = data.get("v_voucher") if isinstance(data, dict) else None
            # 先判断风控凭证：B 站有时会返回 code=0 + 只有 v_voucher 的空结果。
            if code in {-352, -412, -509} or voucher:
                last_error = BilibiliError(
                    f"B 站风控拦截 code={code}",
                    code=code,
                    status=response.status_code,
                )
                if attempt < retry:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                raise last_error
            if code in allowed:
                return payload

            raise BilibiliError(
                f"B 站接口错误 code={code} {payload.get('message') or ''}".strip(),
                code=code,
                status=response.status_code,
            )

        raise last_error or BilibiliError("B 站请求失败")

    # ---------------- WBI 签名 ----------------

    async def _get_mixin_key(self, *, ttl: float = 3600.0) -> str:
        async with self._mixin_lock:
            if self._mixin_key and (time.time() - self._mixin_at) < ttl:
                return self._mixin_key

            payload = await self._request_json(
                "/x/web-interface/nav",
                allowed_codes={0, -101},
                retry=1,
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            wbi = data.get("wbi_img") if isinstance(data, dict) else None
            if not isinstance(wbi, dict):
                raise BilibiliError("无法获取 B 站 WBI 密钥")

            img = str(wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
            sub = str(wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
            raw = img + sub
            if len(raw) < 32:
                raise BilibiliError("B 站 WBI 密钥格式异常")

            self._mixin_key = "".join(raw[index] for index in _MIXIN_TAB if index < len(raw))[:32]
            self._mixin_at = time.time()
            return self._mixin_key

    async def _sign_wbi(self, params: dict[str, Any]) -> dict[str, str]:
        signed = {str(key): str(value) for key, value in params.items() if value is not None}
        signed["wts"] = str(int(time.time()))
        query = urllib.parse.urlencode(sorted(signed.items()))
        query = "".join(char for char in query if char not in "!'()*")
        mixin_key = await self._get_mixin_key()
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed

    # ---------------- 业务接口 ----------------

    async def _search_type(self, keyword: str, page: int, *, wbi: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"search_type": "video", "keyword": keyword, "page": page}
        if wbi:
            params = await self._sign_wbi(params)
            path = "/x/web-interface/wbi/search/type"
        else:
            path = "/x/web-interface/search/type"
        return await self._request_json(path, params, referer=_SEARCH_REFERER, retry=1)

    async def _search_all(self, keyword: str, page: int, *, wbi: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"keyword": keyword, "page": page}
        if wbi:
            params = await self._sign_wbi(params)
            path = "/x/web-interface/wbi/search/all/v2"
        else:
            path = "/x/web-interface/search/all/v2"
        return await self._request_json(path, params, referer=_SEARCH_REFERER, retry=1)

    async def search_videos(self, keyword: str, *, pages: int = 1) -> list[VideoSearchItem]:
        """搜索视频，返回按 B 站结果顺序排列的条目。"""

        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        items: list[VideoSearchItem] = []
        seen: set[str] = set()
        last_error: BilibiliError | None = None

        for page in range(1, max(1, min(int(pages or 1), 5)) + 1):
            payload: dict[str, Any] | None = None
            errors: list[str] = []

            for wbi in (False, True):
                for search_all in (False, True):
                    try:
                        if search_all:
                            payload = await self._search_all(keyword, page, wbi=wbi)
                        else:
                            payload = await self._search_type(keyword, page, wbi=wbi)
                        break
                    except BilibiliError as exc:
                        errors.append(str(exc))
                        last_error = exc
                if payload is not None:
                    break

            if payload is None:
                logger.warning("B 站搜索全部接口失败: keyword=%s page=%s errors=%s", keyword, page, errors)
                break

            for entry in _iter_video_entries(payload):
                item = _parse_search_item(entry)
                if item is not None and item.bvid not in seen:
                    seen.add(item.bvid)
                    items.append(item)

        if not items and last_error is not None:
            raise last_error
        return items

    async def video_info(self, bvid: str) -> VideoInfo:
        """读取视频详情，主要是为了拿到 cid / 真实时长 / UP 主。"""

        bvid = str(bvid or "").strip()
        if not _BV_PATTERN.fullmatch(bvid):
            raise BilibiliError(f"非法 BV 号: {bvid}")

        payload = await self._request_json("/x/web-interface/view", {"bvid": bvid})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BilibiliError("视频详情为空")

        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        cid = _to_int(data.get("cid") or (pages[0].get("cid") if pages and isinstance(pages[0], dict) else 0))
        if not cid:
            raise BilibiliError("视频缺少 cid，无法获取音频")

        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        return VideoInfo(
            bvid=bvid,
            aid=_to_int(data.get("aid") or data.get("avid")),
            cid=cid,
            title=clean_html_text(data.get("title") or ""),
            description=clean_html_text(data.get("desc") or ""),
            duration_s=_to_int(data.get("duration")),
            owner=clean_html_text(owner.get("name") or ""),
            view=_to_int(stat.get("view")),
            like=_to_int(stat.get("like")),
            pubdate=_to_int(data.get("pubdate")),
        )

    async def audio_streams(self, bvid: str, cid: int) -> list[AudioStream]:
        """获取视频可用的音频流；DASH 不可用时回退到 durl 整段 mp4。"""

        payload = await self._request_json(
            "/x/player/playurl",
            {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1},
            referer=f"https://www.bilibili.com/video/{bvid}",
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BilibiliError("播放地址为空")

        streams: list[AudioStream] = []
        dash = data.get("dash") if isinstance(data.get("dash"), dict) else {}

        def add_audio(raw_audio: Any) -> None:
            if not isinstance(raw_audio, dict):
                return
            base_url = str(raw_audio.get("baseUrl") or raw_audio.get("base_url") or "")
            if not base_url:
                return
            backups = raw_audio.get("backupUrl") or raw_audio.get("backup_url") or []
            if not isinstance(backups, list):
                backups = []
            streams.append(
                AudioStream(
                    stream_id=_to_int(raw_audio.get("id")),
                    bandwidth=_to_int(raw_audio.get("bandwidth")),
                    mime_type=str(raw_audio.get("mimeType") or raw_audio.get("mime_type") or ""),
                    codecs=str(raw_audio.get("codecs") or ""),
                    base_url=base_url,
                    backup_urls=tuple(str(url) for url in backups if url),
                    size=_to_int(raw_audio.get("size")),
                )
            )

        audio_list = dash.get("audio")
        if isinstance(audio_list, list):
            for raw_audio in audio_list:
                add_audio(raw_audio)

        for extra_key in ("flac", "dolby"):
            extra = dash.get(extra_key)
            if isinstance(extra, dict):
                extra_audio = extra.get("audio")
                if isinstance(extra_audio, list):
                    for raw_audio in extra_audio:
                        add_audio(raw_audio)
                elif isinstance(extra_audio, dict):
                    add_audio(extra_audio)

        if not streams:
            durl = data.get("durl")
            if isinstance(durl, list):
                for segment in durl:
                    if not isinstance(segment, dict):
                        continue
                    url = str(segment.get("url") or "")
                    if not url:
                        continue
                    backups = segment.get("backup_url") or []
                    if not isinstance(backups, list):
                        backups = []
                    streams.append(
                        AudioStream(
                            stream_id=0,
                            bandwidth=0,
                            mime_type="video/mp4",
                            codecs="",
                            base_url=url,
                            backup_urls=tuple(str(item) for item in backups if item),
                            size=_to_int(segment.get("size")),
                            is_video=True,
                        )
                    )

        if not streams:
            raise BilibiliError("该视频没有可用的音频流")
        return streams

    @staticmethod
    def pick_stream(
        streams: Sequence[AudioStream],
        quality: str = "low",
        *,
        duration_s: int = 0,
        max_bytes: int = 0,
    ) -> AudioStream | None:
        """按音质偏好挑选一条流，并尽量避开超过大小上限的流。"""

        candidates = [stream for stream in streams if stream.base_url]
        if not candidates:
            return None

        def fits(stream: AudioStream) -> bool:
            if not max_bytes:
                return True
            if stream.size and stream.size > max_bytes:
                return False
            if duration_s > 0 and stream.bandwidth > 0:
                estimated = stream.bandwidth * duration_s / 8
                if estimated > max_bytes:
                    return False
            return True

        fitting = [stream for stream in candidates if fits(stream)]
        if max_bytes and not fitting:
            return None
        if fitting:
            candidates = fitting

        normal = [stream for stream in candidates if not stream.is_video]
        pool = normal or candidates

        preference = {
            "low": [30216, 30232, 30280],
            "medium": [30232, 30216, 30280],
            "high": [30280, 30232, 30216],
        }.get(str(quality or "low").lower(), [30216, 30232, 30280])

        for stream_id in preference:
            for stream in pool:
                if stream.stream_id == stream_id:
                    return stream

        ordered = sorted(pool, key=lambda stream: stream.bandwidth or 0)
        if str(quality).lower() == "high":
            return ordered[-1]
        if str(quality).lower() == "medium":
            return ordered[len(ordered) // 2]
        return ordered[0]

    async def download_audio(self, stream: AudioStream, *, max_bytes: int) -> AudioPayload:
        """下载选中的音频流，自动尝试备用 CDN 地址。"""

        urls = [stream.base_url, *stream.backup_urls]
        last_error: Exception | None = None

        for url in urls:
            if not url:
                continue
            try:
                headers = self._base_headers(referer=_VIDEO_REFERER)
                headers["Accept"] = "*/*"
                chunks: list[bytes] = []
                total = 0

                async with self._client.stream(
                    "GET",
                    url,
                    headers=headers,
                    timeout=httpx.Timeout(self.options.download_timeout_seconds),
                ) as response:
                    if response.status_code not in {200, 206}:
                        raise BilibiliError(f"音频下载 HTTP {response.status_code}", status=response.status_code)

                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "text/html" in content_type or "application/json" in content_type:
                        raise BilibiliError("音频地址返回了网页而不是音频")

                    content_length = _to_int(response.headers.get("content-length"))
                    if max_bytes and content_length > max_bytes:
                        raise BilibiliError(f"音频超过大小限制（{content_length} > {max_bytes}）")

                    async for chunk in response.aiter_bytes(64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if max_bytes and total > max_bytes:
                            raise BilibiliError(f"音频超过大小限制（>{max_bytes} 字节）")
                        chunks.append(chunk)

                if total < 512:
                    raise BilibiliError("音频文件过小，可能下载失败")

                data = b"".join(chunks)
                extension = _guess_extension(data, content_type, stream)
                return AudioPayload(
                    data=data,
                    content_type=content_type,
                    extension=extension,
                    stream_id=stream.stream_id,
                )
            except (httpx.HTTPError, BilibiliError) as exc:
                last_error = exc
                logger.info("音频下载失败，尝试下一个地址: %s", exc)
                continue

        raise BilibiliError(f"音频下载失败: {last_error}") from last_error


def _guess_extension(data: bytes, content_type: str, stream: AudioStream) -> str:
    """根据文件头猜测扩展名；SnowLuma 会按内容转码，扩展名主要用于缓存。"""

    if stream.is_video:
        return "mp4"
    head = data[:64]
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if b"ftyp" in head:
        return "m4a"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"RIFF":
        return "wav"
    if "mp4" in content_type or "m4a" in content_type:
        return "m4a"
    return "m4a"
