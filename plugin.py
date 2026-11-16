"""AI 翻唱插件主类。

指令：
    /翻唱 歌名
    /翻唱 歌名 歌手
    /ai翻唱 歌名

流程：
    搜索 B 站“歌名 AI翻唱” → 过滤/打分 → 选最佳候选 → 下载音频
    → 发送标题/UP主/链接 → 以 QQ 语音消息发送音频。

语音走 ``send.custom("voice", base64)``，不依赖 MaiBot 与 NapCat 共享目录；
SnowLuma/NapCat 会自行把 m4a/mp3 转成 QQ silk 语音。
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

try:
    from .bilibili import (
        DEFAULT_USER_AGENT,
        AudioPayload,
        BilibiliClient,
        BilibiliError,
        BilibiliOptions,
        VideoSearchItem,
    )
    from .cache import AudioCache
    from .covers import (
        Candidate,
        COMMAND_PATTERN,
        DEFAULT_CAPTION_TEMPLATE,
        expand_queries,
        extract_song,
        format_caption,
        rank_search_items,
    )
except ImportError:  # pragma: no cover - 仅独立运行时走到这里
    from bilibili import (  # type: ignore[no-redef]
        DEFAULT_USER_AGENT,
        AudioPayload,
        BilibiliClient,
        BilibiliError,
        BilibiliOptions,
        VideoSearchItem,
    )
    from cache import AudioCache  # type: ignore[no-redef]
    from covers import (  # type: ignore[no-redef]
        Candidate,
        COMMAND_PATTERN,
        DEFAULT_CAPTION_TEMPLATE,
        expand_queries,
        extract_song,
        format_caption,
        rank_search_items,
    )


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用 /翻唱 指令")
    config_version: str = Field(default="1.1.0", description="配置版本")


class SearchConfig(PluginConfigBase):
    """B 站搜索配置。"""

    __ui_label__ = "搜索与选源"
    __ui_icon__ = "search"
    __ui_order__ = 1

    query_templates: list[str] = Field(
        default_factory=lambda: ["{song} AI翻唱", "{song} AI cover", "{song} 翻唱"],
        description="依次尝试的搜索关键词模板，{song} 会被替换为歌名",
    )
    max_pages: int = Field(default=1, ge=1, le=3, description="每个关键词最多翻几页")
    max_candidates: int = Field(default=6, ge=1, le=20, description="进入打分排序的候选数量上限")
    min_score: float = Field(default=10.0, ge=0, le=100, description="候选最低分，低于该分数不发")
    require_ai: bool = Field(default=True, description="是否只接受带 AI 翻唱特征的版本")
    allow_non_ai_fallback: bool = Field(default=True, description="找不到 AI 版时是否退而发送普通翻唱")
    min_duration_seconds: int = Field(default=20, ge=5, le=120, description="视频最短时长（秒）")
    max_duration_seconds: int = Field(default=600, ge=60, le=3600, description="视频最长时长（秒）")
    sessdata: str = Field(default="", description="可选的 B 站 SESSDATA，登录后可提高搜索/音质上限")
    user_agent: str = Field(default=DEFAULT_USER_AGENT, description="请求 B 站使用的 User-Agent")
    rate_limit_seconds: float = Field(default=1.0, ge=0.0, le=10.0, description="B 站 API 请求最小间隔（秒）")
    request_timeout_seconds: float = Field(default=15.0, ge=3.0, le=60.0, description="B 站 API 请求超时（秒）")
    use_env_proxy: bool = Field(default=False, description="是否使用环境变量里的 HTTP(S)_PROXY")
    proxy_url: str = Field(default="", description="可选代理地址，例如 http://127.0.0.1:7890")


class AudioConfig(PluginConfigBase):
    """音频下载配置。"""

    __ui_label__ = "音频"
    __ui_icon__ = "music"
    __ui_order__ = 2

    quality: Literal["low", "medium", "high"] = Field(
        default="low",
        description="音质偏好。语音最终会被压成 silk，low（64k）足够且更省流量",
    )
    max_file_size_mb: int = Field(default=9, ge=1, le=11, description="单个音频文件大小上限（MB）")
    download_timeout_seconds: float = Field(default=60.0, ge=10.0, le=180.0, description="音频下载超时（秒）")
    cache_enabled: bool = Field(default=True, description="是否缓存下载过的音频")
    cache_expire_hours: int = Field(default=72, ge=1, le=720, description="缓存过期时间（小时）")
    cache_max_size_mb: int = Field(default=512, ge=32, le=4096, description="缓存总容量上限（MB）")


class DeliveryConfig(PluginConfigBase):
    """发送行为配置。"""

    __ui_label__ = "发送"
    __ui_icon__ = "send"
    __ui_order__ = 3

    send_ack: bool = Field(default=False, description="开始搜索前是否先回一句“正在找”（默认关闭，只发语音）")
    ack_text: str = Field(
        default="收到～正在找《{song}》的 AI 翻唱，稍等一下哦",
        description="等待提示文案，可用 {song}；仅在 send_ack=true 时发送",
    )
    send_caption: bool = Field(default=False, description="发语音前是否先发标题、UP 主和原视频链接（默认关闭）")
    caption_template: str = Field(
        default=DEFAULT_CAPTION_TEMPLATE,
        description="文案模板，可用 {title} {author} {duration} {url} {song}；仅在 send_caption=true 时发送",
    )
    non_ai_note: str = Field(
        default="",
        description="退而发送普通翻唱时追加的提示；留空表示不提示",
    )
    send_done_text: bool = Field(default=False, description="发送成功后是否再发一句提示")
    done_text: str = Field(default="翻唱语音发好啦～", description="成功提示文案")
    fallback_to_link: bool = Field(default=False, description="音频失败时是否发送原视频链接兜底（默认关闭）")
    failure_text: str = Field(
        default="找到《{song}》的翻唱了，但音频下载/发送失败，可以先点链接听听：{url}",
        description="音频失败兜底文案，可用 {song} {url}",
    )
    no_result_text: str = Field(
        default="没有找到《{song}》的 AI 翻唱，换个歌名或歌手试试？",
        description="没有候选时的文案，可用 {song}",
    )
    search_failed_text: str = Field(
        default="B 站搜索暂时失败了，可能是网络或风控问题，稍后再试试吧",
        description="搜索接口全部失败时的文案",
    )
    voice_storage: bool = Field(default=False, description="是否把语音二进制写入聊天历史（会占用数据库空间）")
    max_attempts: int = Field(default=2, ge=1, le=4, description="音频失败时最多尝试几个候选")


class LimitsConfig(PluginConfigBase):
    """使用限制配置。"""

    __ui_label__ = "限制"
    __ui_icon__ = "shield"
    __ui_order__ = 4

    cooldown_seconds: int = Field(default=20, ge=0, le=600, description="同一聊天流的冷却时间（秒）")
    daily_limit: int = Field(default=10, ge=0, le=1000, description="每个聊天流每日点歌上限，0 表示不限制")
    max_concurrent: int = Field(default=2, ge=1, le=5, description="同时处理的翻唱请求数上限")
    allow_group: bool = Field(default=True, description="是否允许群聊使用")
    allow_private: bool = Field(default=True, description="是否允许私聊使用")
    allowed_group_ids: list[str] = Field(
        default_factory=list,
        description="允许的群号列表，留空表示所有群；填写后仅这些群可用",
    )
    allowed_user_ids: list[str] = Field(
        default_factory=list,
        description="允许的 QQ 号列表，留空表示所有用户；填写后仅这些用户可用",
    )


class AiCoverPluginConfig(PluginConfigBase):
    """AI 翻唱插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

_MAX_BASE64_BYTES = 14_000_000


def safe_format(template: str, default: str, **values: Any) -> str:
    """渲染用户可配置文案；模板写错时退回默认模板。"""

    for candidate in (template, default):
        try:
            rendered = str(candidate or "").format(**values).strip()
        except (KeyError, IndexError, ValueError):
            continue
        if rendered:
            return rendered
    return default


def _is_send_ok(result: Any) -> bool:
    """兼容 SDK 返回 bool 或 ``{"sent": bool}`` 两种形式。"""

    if isinstance(result, dict):
        return bool(result.get("sent"))
    return bool(result)


# ---------------------------------------------------------------------------
# 插件
# ---------------------------------------------------------------------------


class AiCoverPlugin(MaiBotPlugin):
    """搜索并发送 B 站 AI 翻唱语音。"""

    config_model = AiCoverPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._client: BilibiliClient | None = None
        self._cache: AudioCache | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._service_lock = asyncio.Lock()
        self._cooldowns: dict[str, float] = {}
        self._daily_counts: dict[str, tuple[date, int]] = {}

    # ---------------- 生命周期 ----------------

    async def on_load(self) -> None:
        """插件加载：初始化 B 站客户端和音频缓存。"""

        await self._ensure_services()
        self.ctx.logger.info("AI 翻唱插件已加载，使用 /翻唱 歌名 触发")

    async def on_unload(self) -> None:
        """插件卸载：关闭 HTTP 连接。"""

        await self._close_services()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后重建客户端，让超时/代理等设置立即生效。"""

        del scope, config_data, version
        await self._close_services()

    async def _ensure_services(self) -> None:
        """懒加载运行期依赖。"""

        async with self._service_lock:
            cfg = self.config
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(max(1, int(cfg.limits.max_concurrent)))

            if self._client is None:
                try:
                    self._client = BilibiliClient(
                        BilibiliOptions(
                            sessdata=cfg.search.sessdata,
                            user_agent=cfg.search.user_agent or DEFAULT_USER_AGENT,
                            rate_limit_seconds=float(cfg.search.rate_limit_seconds),
                            request_timeout_seconds=float(cfg.search.request_timeout_seconds),
                            download_timeout_seconds=float(cfg.audio.download_timeout_seconds),
                            use_env_proxy=bool(cfg.search.use_env_proxy),
                            proxy_url=str(cfg.search.proxy_url or "").strip(),
                        )
                    )
                except Exception:
                    self.ctx.logger.exception("B 站客户端初始化失败，请检查代理/网络配置")
                    self._client = None

            if cfg.audio.cache_enabled:
                if self._cache is None:
                    try:
                        cache_dir = Path(self.ctx.paths.data_dir) / "audio_cache"
                        cache = AudioCache(
                            cache_dir,
                            max_bytes=int(cfg.audio.cache_max_size_mb) * 1024 * 1024,
                            expire_seconds=int(cfg.audio.cache_expire_hours) * 3600,
                        )
                        await cache.initialize()
                        self._cache = cache
                    except Exception:
                        self.ctx.logger.warning("音频缓存初始化失败，将不使用缓存", exc_info=True)
                        self._cache = None
            else:
                self._cache = None

    async def _close_services(self) -> None:
        async with self._service_lock:
            client = self._client
            self._client = None
            self._cache = None
            self._semaphore = None
        if client is not None:
            await client.close()

    async def _get_client(self) -> BilibiliClient:
        await self._ensure_services()
        if self._client is None:
            raise RuntimeError("B 站客户端初始化失败")
        return self._client

    # ---------------- 指令 ----------------

    @Command(
        "ai_cover",
        description="搜索并发送指定歌曲的 AI 翻唱语音；支持 /翻唱 歌名 和 @机器人 翻唱XX",
        pattern=COMMAND_PATTERN,
        timeout_ms=180000,
    )
    async def handle_cover(
        self,
        stream_id: str = "",
        text: str = "",
        matched_groups: dict[str, Any] | None = None,
        group_id: str = "",
        user_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        """处理 /翻唱 指令。"""

        del kwargs
        cfg = self.config
        if not cfg.plugin.enabled:
            return False, "插件已停用", True

        song = extract_song(text, matched_groups)
        if not song:
            await self.ctx.send.text(
                "用法：/翻唱 歌名\n例如：/翻唱 晴天\n也可以带上歌手：/翻唱 晴天 周杰伦",
                stream_id,
            )
            return False, "缺少歌名", True

        if not stream_id:
            return False, "缺少聊天流", True

        if not self._scope_allowed(group_id, user_id):
            await self.ctx.send.text("这个功能在当前会话没有开启哦", stream_id)
            return False, "会话未授权", True

        now = time.monotonic()
        cooldown_until = self._cooldowns.get(stream_id, 0.0)
        if cooldown_until > now:
            await self.ctx.send.text(
                f"先歇 {max(1, int(cooldown_until - now) + 1)} 秒，刚刚才点过一首～",
                stream_id,
            )
            return False, "冷却中", True

        if not self._consume_daily(stream_id):
            await self.ctx.send.text(
                f"今天已经点过 {cfg.limits.daily_limit} 首啦，明天再来吧～",
                stream_id,
            )
            return False, "达到每日上限", True

        self._cooldowns[stream_id] = now + max(0, int(cfg.limits.cooldown_seconds))

        if cfg.delivery.send_ack:
            ack = safe_format(
                cfg.delivery.ack_text,
                "收到～正在找《{song}》的 AI 翻唱，稍等一下哦",
                song=song,
            )
            await self.ctx.send.text(ack, stream_id)

        try:
            await self._ensure_services()
            semaphore = self._semaphore
            if semaphore is None:
                raise RuntimeError("插件尚未初始化")
            async with semaphore:
                sent = await asyncio.wait_for(self._run_cover(song, stream_id), timeout=170.0)
            return (True, "已发送 AI 翻唱语音", True) if sent else (False, "没有发送", True)
        except asyncio.TimeoutError:
            await self.ctx.send.text("找歌超时了，可能是网络或 B 站抽风，稍后再试试吧", stream_id)
            return False, "超时", True
        except Exception:
            self.ctx.logger.exception("处理 /翻唱 指令失败: %s", song)
            await self.ctx.send.text("翻唱获取失败了，稍后再试试吧～", stream_id)
            return False, "异常", True

    # ---------------- 业务主流程 ----------------

    async def _run_cover(self, song: str, stream_id: str) -> bool:
        cfg = self.config
        client = await self._get_client()

        try:
            items = await self._collect_search_items(client, song)
        except BilibiliError as exc:
            self.ctx.logger.warning("B 站搜索失败: %s", exc)
            await self.ctx.send.text(cfg.delivery.search_failed_text, stream_id)
            return False

        if not items:
            text = safe_format(cfg.delivery.no_result_text, "没有找到《{song}》的 AI 翻唱，换个歌名或歌手试试？", song=song)
            await self.ctx.send.text(text, stream_id)
            return False

        common = {
            "min_duration": int(cfg.search.min_duration_seconds),
            "max_duration": int(cfg.search.max_duration_seconds),
            "min_score": float(cfg.search.min_score),
            "limit": int(cfg.search.max_candidates),
        }
        candidates = rank_search_items(items, song, require_ai=bool(cfg.search.require_ai), **common)
        non_ai_fallback = False
        if not candidates and cfg.search.require_ai and cfg.search.allow_non_ai_fallback:
            candidates = rank_search_items(items, song, require_ai=False, **common)
            non_ai_fallback = bool(candidates)

        if not candidates:
            text = safe_format(cfg.delivery.no_result_text, "没有找到《{song}》的 AI 翻唱，换个歌名或歌手试试？", song=song)
            await self.ctx.send.text(text, stream_id)
            return False

        self.ctx.logger.info(
            "《%s》候选: %s",
            song,
            [(candidate.bvid, candidate.score, candidate.is_ai) for candidate in candidates[:5]],
        )

        top_url = candidates[0].item.url
        for candidate in candidates[: max(1, int(cfg.delivery.max_attempts))]:
            try:
                result = await self._try_candidate(
                    client,
                    candidate,
                    song,
                    stream_id,
                    non_ai=non_ai_fallback,
                )
            except Exception as exc:
                self.ctx.logger.warning("候选处理异常 %s: %s", candidate.bvid, exc)
                result = "skipped"

            if result == "sent":
                return True
            if result == "send_failed":
                # 语音通道本身发不出去，换候选也没用；_try_candidate 已提示过。
                return False

        if cfg.delivery.fallback_to_link:
            text = safe_format(
                cfg.delivery.failure_text,
                "找到《{song}》的翻唱了，但音频下载/发送失败，可以先点链接听听：{url}",
                song=song,
                url=top_url,
            )
            await self.ctx.send.text(text, stream_id)
        return False

    async def _collect_search_items(self, client: BilibiliClient, song: str) -> list[VideoSearchItem]:
        """按关键词模板依次搜索并去重。"""

        cfg = self.config
        queries = expand_queries(song, cfg.search.query_templates)
        items: list[VideoSearchItem] = []
        seen: set[str] = set()
        errors: list[BilibiliError] = []

        for query in queries:
            try:
                page_items = await client.search_videos(query, pages=int(cfg.search.max_pages))
            except BilibiliError as exc:
                self.ctx.logger.warning("搜索「%s」失败: %s", query, exc)
                errors.append(exc)
                continue

            self.ctx.logger.info("搜索「%s」得到 %d 条结果", query, len(page_items))
            for item in page_items:
                if item.bvid in seen:
                    continue
                seen.add(item.bvid)
                items.append(item)

        if not items and errors:
            raise errors[-1]
        return items

    async def _try_candidate(
        self,
        client: BilibiliClient,
        candidate: Candidate,
        song: str,
        stream_id: str,
        *,
        non_ai: bool,
    ) -> str:
        """尝试发送一个候选，返回 ``sent`` / ``skipped`` / ``send_failed``。"""

        cfg = self.config
        item = candidate.item

        try:
            info = await client.video_info(item.bvid)
        except BilibiliError as exc:
            self.ctx.logger.warning("读取视频详情失败 %s: %s", item.bvid, exc)
            return "skipped"

        duration = int(info.duration_s or item.duration_s or 0)
        if duration and (
            duration < int(cfg.search.min_duration_seconds)
            or duration > int(cfg.search.max_duration_seconds)
        ):
            self.ctx.logger.info("跳过长视频 %s: %ss", item.bvid, duration)
            return "skipped"

        try:
            audio = await self._get_audio(client, item.bvid, info.cid, duration)
        except BilibiliError as exc:
            self.ctx.logger.warning("获取音频失败 %s: %s", item.bvid, exc)
            return "skipped"

        if audio is None:
            self.ctx.logger.info("没有合适的音频流，跳过 %s", item.bvid)
            return "skipped"

        encoded = base64.b64encode(audio.data)
        if len(encoded) > _MAX_BASE64_BYTES:
            self.ctx.logger.warning(
                "音频超过 base64 帧限制，跳过 %s: %d bytes",
                item.bvid,
                len(audio.data),
            )
            return "skipped"

        caption_sent = False
        if cfg.delivery.send_caption:
            caption = format_caption(
                cfg.delivery.caption_template,
                title=info.title or item.title,
                author=info.owner or item.author,
                duration=duration,
                url=item.url,
                song=song,
                note=cfg.delivery.non_ai_note if non_ai else "",
            )
            await self.ctx.send.text(caption, stream_id)
            caption_sent = True
        elif non_ai and str(cfg.delivery.non_ai_note or "").strip():
            await self.ctx.send.text(cfg.delivery.non_ai_note, stream_id)

        ok = await self._send_voice(encoded.decode("ascii"), stream_id)
        if ok:
            self.ctx.logger.info("已发送翻唱语音: %s %s", item.bvid, info.title or item.title)
            if cfg.delivery.send_done_text:
                done = safe_format(cfg.delivery.done_text, "翻唱语音发好啦～", song=song)
                await self.ctx.send.text(done, stream_id)
            return "sent"

        self.ctx.logger.warning("语音发送失败: %s", item.bvid)
        if cfg.delivery.fallback_to_link:
            if caption_sent:
                await self.ctx.send.text("语音没发出去，点上面的链接直接听吧～", stream_id)
            else:
                text = safe_format(
                    cfg.delivery.failure_text,
                    "找到《{song}》的翻唱了，但音频下载/发送失败，可以先点链接听听：{url}",
                    song=song,
                    url=item.url,
                )
                await self.ctx.send.text(text, stream_id)
        return "send_failed"

    async def _get_audio(self, client: BilibiliClient, bvid: str, cid: int, duration: int) -> AudioPayload | None:
        """优先读缓存，未命中则获取播放地址并下载。"""

        cfg = self.config
        max_bytes = int(cfg.audio.max_file_size_mb) * 1024 * 1024

        if self._cache is not None:
            cached = await self._cache.get(bvid)
            if cached is not None and len(cached.data) <= max_bytes:
                self.ctx.logger.info("命中音频缓存: %s (%d bytes)", bvid, len(cached.data))
                return AudioPayload(
                    data=cached.data,
                    content_type="application/octet-stream",
                    extension=cached.extension,
                    stream_id=0,
                )

        streams = await client.audio_streams(bvid, cid)
        stream = client.pick_stream(
            streams,
            cfg.audio.quality,
            duration_s=duration,
            max_bytes=max_bytes,
        )
        if stream is None and cfg.audio.quality != "low":
            stream = client.pick_stream(streams, "low", duration_s=duration, max_bytes=max_bytes)
        if stream is None:
            return None

        payload = await client.download_audio(stream, max_bytes=max_bytes)
        if self._cache is not None:
            await self._cache.put(bvid, payload.data, payload.extension)
        return payload

    async def _send_voice(self, encoded_audio: str, stream_id: str) -> bool:
        """以 QQ 语音消息发送 base64 音频。"""

        cfg = self.config
        if not encoded_audio:
            return False
        try:
            result = await self.ctx.send.custom(
                "voice",
                encoded_audio,
                stream_id,
                storage_message=bool(cfg.delivery.voice_storage),
            )
        except Exception:
            self.ctx.logger.exception("调用 send.custom(voice) 失败")
            return False
        return _is_send_ok(result)

    # ---------------- 限制 ----------------

    def _scope_allowed(self, group_id: str, user_id: str) -> bool:
        cfg = self.config.limits
        group_id = str(group_id or "").strip()
        user_id = str(user_id or "").strip()

        if group_id:
            if not cfg.allow_group:
                return False
            allowed_groups = {str(item).strip() for item in cfg.allowed_group_ids if str(item).strip()}
            if allowed_groups and group_id not in allowed_groups:
                return False
        else:
            if not cfg.allow_private:
                return False

        allowed_users = {str(item).strip() for item in cfg.allowed_user_ids if str(item).strip()}
        if allowed_users and user_id not in allowed_users:
            return False
        return True

    def _consume_daily(self, stream_id: str) -> bool:
        limit = int(self.config.limits.daily_limit)
        if limit <= 0:
            return True

        today = date.today()
        entry = self._daily_counts.get(stream_id)
        if entry is not None and entry[0] == today:
            if entry[1] >= limit:
                return False
            self._daily_counts[stream_id] = (today, entry[1] + 1)
        else:
            self._daily_counts[stream_id] = (today, 1)

        if len(self._daily_counts) > 1000:
            self._daily_counts = {
                key: value for key, value in self._daily_counts.items() if value[0] == today
            }
        return True


def create_plugin() -> AiCoverPlugin:
    """创建插件实例。"""

    return AiCoverPlugin()
