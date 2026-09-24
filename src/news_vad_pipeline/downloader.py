from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import PipelineConfig


def find_downloaded_audio(raw_dir: Path, video_id: str) -> Path | None:
    ignored_suffixes = {".part", ".ytdl", ".json", ".description"}
    candidates = [
        path
        for path in raw_dir.glob(f"{video_id}.*")
        if path.is_file() and path.suffix.casefold() not in ignored_suffixes
    ]
    return max(candidates, key=lambda path: path.stat().st_size) if candidates else None


def download_audio(video: dict[str, Any], config: PipelineConfig) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run: python -m pip install -r requirements.txt") from exc

    video_id = str(video["video_id"])
    existing = find_downloaded_audio(config.raw_dir, video_id)
    if existing:
        print(f"[DOWNLOAD] reuse={existing}", flush=True)
        return existing

    cookie_file = os.environ.get("YTDLP_COOKIE_FILE", "").strip()
    output_template = str(config.raw_dir / f"{video_id}.%(ext)s")
    options: dict[str, Any] = {
        "format": config.download_format,
        "outtmpl": output_template,
        "noplaylist": True,
        "continuedl": True,
        "overwrites": False,
        "retries": config.download_retries,
        "fragment_retries": config.fragment_retries,
        "concurrent_fragment_downloads": config.concurrent_fragments,
        "socket_timeout": config.socket_timeout_sec,
        "sleep_interval": config.sleep_interval_sec,
        "download_archive": str(config.download_archive_path),
        "restrictfilenames": True,
        "writethumbnail": False,
        "writeinfojson": False,
        "quiet": False,
        "no_warnings": False,
    }
    if cookie_file:
        options["cookiefile"] = cookie_file

    config.raw_dir.mkdir(parents=True, exist_ok=True)
    print(f"[DOWNLOAD] video_id={video_id} title={video.get('title', '')}", flush=True)
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.extract_info(str(video["webpage_url"]), download=True)
    downloaded = find_downloaded_audio(config.raw_dir, video_id)
    if not downloaded:
        raise RuntimeError(
            f"yt-dlp returned without a media file for {video_id}. "
            "If the archive is stale, remove only this video ID from state/yt_dlp_archive.txt."
        )
    return downloaded
