#!/usr/bin/env python3
"""命令行验证 B 站搜索、候选打分和音频下载（不会发送 QQ 消息）。

用法：
    python tools/check_bilibili.py "晴天"
    python tools/check_bilibili.py "晴天" --download
    python tools/check_bilibili.py "晴天" --sessdata "xxx" --rate 2.0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili import BilibiliClient, BilibiliError, BilibiliOptions  # noqa: E402
from covers import DEFAULT_QUERY_TEMPLATES, expand_queries, rank_search_items  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    song = " ".join(args.song).strip()
    if not song:
        print("请提供歌名，例如：python tools/check_bilibili.py 晴天")
        return 2

    client = BilibiliClient(
        BilibiliOptions(
            sessdata=args.sessdata,
            rate_limit_seconds=args.rate,
            request_timeout_seconds=args.timeout,
            download_timeout_seconds=args.download_timeout,
            use_env_proxy=args.env_proxy,
            proxy_url=args.proxy,
        )
    )

    try:
        queries = expand_queries(song, DEFAULT_QUERY_TEMPLATES)
        items = []
        seen: set[str] = set()
        for query in queries:
            try:
                batch = await client.search_videos(query, pages=args.pages)
            except BilibiliError as exc:
                print(f"[搜索失败] {query}: {exc}")
                continue
            print(f"[搜索] {query}: {len(batch)} 条")
            for item in batch:
                if item.bvid not in seen:
                    seen.add(item.bvid)
                    items.append(item)

        if not items:
            print("没有拿到任何搜索结果。")
            return 1

        candidates = rank_search_items(
            items,
            song,
            require_ai=True,
            min_score=0.0,
            max_duration=3600,
            limit=args.limit,
        )
        if not candidates and not args.ai_only:
            print("AI 候选为空，下面展示普通翻唱候选：")
            candidates = rank_search_items(
                items,
                song,
                require_ai=False,
                min_score=0.0,
                max_duration=3600,
                limit=args.limit,
            )

        if not candidates:
            print("没有通过过滤的候选。")
            return 1

        for index, candidate in enumerate(candidates, start=1):
            item = candidate.item
            print(
                f"{index}. [{candidate.score}] {item.title} | "
                f"UP={item.author} | {item.duration_s}s | play={item.play} | "
                f"AI={candidate.is_ai} | {item.url}"
            )
            if candidate.reasons:
                print("   依据:", "；".join(candidate.reasons))

        if args.download:
            top = candidates[0].item
            print(f"\n[下载] 选择第一名 {top.bvid}")
            info = await client.video_info(top.bvid)
            print(f"[详情] {info.title} | {info.duration_s}s | cid={info.cid}")
            streams = await client.audio_streams(top.bvid, info.cid)
            print("[音频流]", [(stream.stream_id, stream.bandwidth, stream.is_video) for stream in streams])
            stream = client.pick_stream(
                streams,
                args.quality,
                duration_s=info.duration_s,
                max_bytes=args.max_mb * 1024 * 1024,
            )
            if stream is None:
                print("没有符合大小限制的音频流。")
                return 1
            payload = await client.download_audio(stream, max_bytes=args.max_mb * 1024 * 1024)
            print(
                f"[下载成功] {len(payload.data)} bytes | ext={payload.extension} "
                f"| content-type={payload.content_type} | head={payload.data[:16]!r}"
            )
    finally:
        await client.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查 B 站 AI 翻唱搜索与下载链路")
    parser.add_argument("song", nargs="+", help="歌名，可以带歌手，例如：晴天 周杰伦")
    parser.add_argument("--pages", type=int, default=1, help="每个关键词翻页数（默认 1）")
    parser.add_argument("--limit", type=int, default=10, help="展示候选数量（默认 10）")
    parser.add_argument("--quality", choices=["low", "medium", "high"], default="low", help="音质偏好")
    parser.add_argument("--max-mb", type=int, default=9, help="音频大小上限 MB（默认 9）")
    parser.add_argument("--download", action="store_true", help="下载第一名音频并打印信息")
    parser.add_argument("--ai-only", action="store_true", help="只展示 AI 候选")
    parser.add_argument("--sessdata", default="", help="可选 B 站 SESSDATA")
    parser.add_argument("--rate", type=float, default=1.0, help="API 请求间隔秒数（默认 1.0）")
    parser.add_argument("--timeout", type=float, default=15.0, help="API 超时秒数")
    parser.add_argument("--download-timeout", type=float, default=60.0, help="下载超时秒数")
    parser.add_argument("--env-proxy", action="store_true", help="使用环境变量代理")
    parser.add_argument("--proxy", default="", help="显式代理地址，例如 http://127.0.0.1:7890")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
