from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PipelineConfig:
    root: Path
    config_path: Path
    target_hours: float
    source_urls: tuple[str, ...]
    require_news_keyword: bool
    news_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    min_video_duration_sec: float
    max_video_duration_sec: float
    max_attempts_per_video: int
    sample_rate: int
    channels: int
    sample_width_bytes: int
    min_segment_sec: float
    max_segment_sec: float
    vad_model_id: str
    vad_device: str
    min_duration_on: float
    min_duration_off: float
    download_format: str
    download_retries: int
    fragment_retries: int
    concurrent_fragments: int
    socket_timeout_sec: int
    sleep_interval_sec: float
    keep_downloaded_source: bool
    keep_full_wav: bool
    report_every_videos: int
    raw_dir: Path
    wav_dir: Path
    segments_dir: Path
    state_dir: Path
    result_dir: Path
    hf_cache_dir: Path

    @property
    def database_path(self) -> Path:
        return self.state_dir / "pipeline.sqlite3"

    @property
    def download_archive_path(self) -> Path:
        return self.state_dir / "yt_dlp_archive.txt"

    def ensure_directories(self) -> None:
        for path in [
            self.raw_dir,
            self.wav_dir,
            self.segments_dir,
            self.state_dir,
            self.result_dir,
            self.hf_cache_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Missing [{name}] section in config")
    return value


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def load_config(path: str | Path) -> PipelineConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    root = config_path.parent
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    project = _section(data, "project")
    audio = _section(data, "audio")
    vad = _section(data, "vad")
    download = _section(data, "download")
    storage = _section(data, "storage")
    runtime = _section(data, "runtime")
    paths = _section(data, "paths")

    config = PipelineConfig(
        root=root,
        config_path=config_path,
        target_hours=float(project["target_hours"]),
        source_urls=tuple(str(item) for item in project["source_urls"]),
        require_news_keyword=bool(project["require_news_keyword"]),
        news_keywords=tuple(str(item) for item in project["news_keywords"]),
        exclude_keywords=tuple(str(item) for item in project["exclude_keywords"]),
        min_video_duration_sec=float(project["min_video_duration_sec"]),
        max_video_duration_sec=float(project["max_video_duration_sec"]),
        max_attempts_per_video=int(project["max_attempts_per_video"]),
        sample_rate=int(audio["sample_rate"]),
        channels=int(audio["channels"]),
        sample_width_bytes=int(audio["sample_width_bytes"]),
        min_segment_sec=float(audio["min_segment_sec"]),
        max_segment_sec=float(audio["max_segment_sec"]),
        vad_model_id=str(vad["model_id"]),
        vad_device=str(vad["device"]),
        min_duration_on=float(vad["min_duration_on"]),
        min_duration_off=float(vad["min_duration_off"]),
        download_format=str(download["format"]),
        download_retries=int(download["retries"]),
        fragment_retries=int(download["fragment_retries"]),
        concurrent_fragments=int(download["concurrent_fragments"]),
        socket_timeout_sec=int(download["socket_timeout_sec"]),
        sleep_interval_sec=float(download["sleep_interval_sec"]),
        keep_downloaded_source=bool(storage["keep_downloaded_source"]),
        keep_full_wav=bool(storage["keep_full_wav"]),
        report_every_videos=int(runtime["report_every_videos"]),
        raw_dir=_resolve(root, str(paths["raw_dir"])),
        wav_dir=_resolve(root, str(paths["wav_dir"])),
        segments_dir=_resolve(root, str(paths["segments_dir"])),
        state_dir=_resolve(root, str(paths["state_dir"])),
        result_dir=_resolve(root, str(paths["result_dir"])),
        hf_cache_dir=_resolve(root, str(paths["hf_cache_dir"])),
    )
    validate_config(config)
    return config


def validate_config(config: PipelineConfig) -> None:
    if config.target_hours <= 0 or config.target_hours > 1000:
        raise ValueError("target_hours must be in (0, 1000]")
    if not config.source_urls:
        raise ValueError("At least one source URL is required")
    if config.min_video_duration_sec <= 0:
        raise ValueError("min_video_duration_sec must be positive")
    if config.max_video_duration_sec <= config.min_video_duration_sec:
        raise ValueError("max_video_duration_sec must be greater than min_video_duration_sec")
    if config.min_segment_sec < 5.0:
        raise ValueError("min_segment_sec must be at least 5 seconds")
    if config.max_segment_sec > 30.0:
        raise ValueError("max_segment_sec must be at most 30 seconds")
    if config.max_segment_sec <= config.min_segment_sec:
        raise ValueError("max_segment_sec must be greater than min_segment_sec")
    if config.sample_rate <= 0 or config.channels != 1 or config.sample_width_bytes != 2:
        raise ValueError("Output audio must be mono PCM16 with a positive sample rate")
    if config.vad_device not in {"auto", "cpu", "cuda"}:
        raise ValueError("vad.device must be auto, cpu, or cuda")
    if config.max_attempts_per_video < 1:
        raise ValueError("max_attempts_per_video must be >= 1")
    if config.report_every_videos < 1:
        raise ValueError("report_every_videos must be >= 1")
