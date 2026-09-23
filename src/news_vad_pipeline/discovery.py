from __future__ import annotations

import os
from collections import Counter
from typing import Any

from .config import PipelineConfig
from .db import PipelineDB
from .utils import keyword_match


def _webpage_url(entry: dict[str, Any], video_id: str) -> str:
    value = str(entry.get("webpage_url") or entry.get("url") or "")
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://www.youtube.com/watch?v={video_id}"


def _filter_reason(entry: dict[str, Any], config: PipelineConfig) -> str:
    title = str(entry.get("title") or "")
    duration = float(entry.get("duration") or 0)
    live_status = str(entry.get("live_status") or "")
    if live_status in {"is_live", "is_upcoming", "post_live"}:
        return f"live_status:{live_status}"
    if duration <= 0:
        return "missing_duration"
    if duration < config.min_video_duration_sec:
        return "too_short"
    if duration > config.max_video_duration_sec:
        return "too_long"
    if keyword_match(title, config.exclude_keywords):
        return "excluded_keyword"
    if config.require_news_keyword and not keyword_match(title, config.news_keywords):
        return "missing_news_keyword"
    return ""


def discover_videos(
    config: PipelineConfig,
    db: PipelineDB,
    *,
    max_videos_per_source: int = 0,
) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run: python -m pip install -r requirements.txt") from exc

    cookie_file = os.environ.get("YTDLP_COOKIE_FILE", "").strip()
    options: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": False,
        "lazy_playlist": False,
    }
    if max_videos_per_source > 0:
        options["playlistend"] = max_videos_per_source
    if cookie_file:
        options["cookiefile"] = cookie_file

    discovered = 0
    selected = 0
    selected_seconds = 0.0
    reasons: Counter[str] = Counter()
    for source_url in config.source_urls:
        print(f"[DISCOVER] source={source_url}", flush=True)
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=False)
        if not info:
            print(f"[DISCOVER] no metadata returned for {source_url}", flush=True)
            continue
        entries = info.get("entries") or [info]
        for entry in entries:
            if not entry:
                reasons["metadata_error"] += 1
                continue
            video_id = str(entry.get("id") or "").strip()
            if not video_id:
                reasons["missing_video_id"] += 1
                continue
            reason = _filter_reason(entry, config)
            is_selected = not reason
            duration = float(entry.get("duration") or 0)
            item = {
                "video_id": video_id,
                "webpage_url": _webpage_url(entry, video_id),
                "title": str(entry.get("title") or ""),
                "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                "upload_date": str(entry.get("upload_date") or ""),
                "duration_sec": duration,
                "source_url": source_url,
                "selected": is_selected,
                "filter_reason": reason,
                "metadata": {
                    "playlist_id": entry.get("playlist_id"),
                    "playlist_index": entry.get("playlist_index"),
                    "live_status": entry.get("live_status"),
                    "availability": entry.get("availability"),
                },
            }
            db.upsert_video(item)
            discovered += 1
            if is_selected:
                selected += 1
                selected_seconds += duration
            else:
                reasons[reason] += 1
        db.commit()

    result = {
        "discovered": discovered,
        "selected": selected,
        "selected_raw_hours": selected_seconds / 3600.0,
        "filtered_reasons": dict(reasons),
    }
    db.set_metadata("last_discovery", result)
    print(
        f"[DISCOVER] discovered={discovered} selected={selected} "
        f"selected_raw_hours={result['selected_raw_hours']:.2f}",
        flush=True,
    )
    return result
