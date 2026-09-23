from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .audio import SpeechRegion, convert_to_wav, extract_segments, plan_segments, probe_wav
from .config import PipelineConfig
from .db import PipelineDB
from .discovery import discover_videos
from .downloader import download_audio
from .report import generate_report
from .utils import safe_unlink
from .vad import PyannoteVAD


def _cleanup_file(path: Path, allowed_parent: Path, label: str) -> bool:
    try:
        return safe_unlink(path, allowed_parent)
    except (OSError, ValueError) as exc:
        print(f"[WARNING] could not remove {label}={path}: {exc}", flush=True)
        return False


def _existing_wav(video: dict[str, Any], config: PipelineConfig) -> Path | None:
    stored = str(video.get("wav_path") or "")
    candidates = [Path(stored)] if stored else []
    candidates.append(config.wav_dir / f"{video['video_id']}.wav")
    for candidate in candidates:
        if candidate.is_file():
            try:
                info = probe_wav(candidate)
            except Exception:
                continue
            expected = (config.sample_rate, config.channels, config.sample_width_bytes)
            actual = (info["sample_rate"], info["channels"], info["sample_width_bytes"])
            if actual == expected:
                return candidate
    return None


def run_pipeline(
    config: PipelineConfig,
    db: PipelineDB,
    *,
    target_hours: float | None = None,
    max_videos: int = 0,
    discovery_limit: int = 0,
) -> dict[str, Any]:
    config.ensure_directories()
    target = float(target_hours if target_hours is not None else config.target_hours)
    if target <= 0 or target > 1000:
        raise ValueError("target-hours must be in (0, 1000]")
    db.set_metadata("target_hours", target)
    recovered = db.recover_interrupted()
    if recovered:
        print(f"[RESUME] recovered_interrupted_videos={recovered}", flush=True)

    if db.selected_count() == 0:
        discover_videos(config, db, max_videos_per_source=discovery_limit)
    current_seconds = db.valid_seconds()
    target_seconds = target * 3600.0
    if current_seconds >= target_seconds:
        print(f"[DONE] target already reached: {current_seconds / 3600.0:.3f}h", flush=True)
        return generate_report(config, db)

    selected_raw_hours = db.selected_raw_seconds() / 3600.0
    if selected_raw_hours < target:
        print(
            f"[WARNING] selected raw duration is only {selected_raw_hours:.2f}h, "
            f"below target={target:.2f}h. Add sources or relax title filters.",
            flush=True,
        )

    candidates = db.selected_videos(config.max_attempts_per_video)
    if max_videos > 0:
        candidates = candidates[:max_videos]
    vad: PyannoteVAD | None = None
    run_started = time.perf_counter()
    for position, row in enumerate(candidates, start=1):
        video = dict(row)
        video_id = str(video["video_id"])
        stage = "download"
        print(
            f"[VIDEO] {position}/{len(candidates)} id={video_id} "
            f"duration={float(video['duration_sec']) / 60.0:.1f}m "
            f"valid={current_seconds / 3600.0:.3f}/{target:.3f}h",
            flush=True,
        )
        db.increment_attempt(video_id, "downloading")
        source_path: Path | None = None
        wav_path: Path | None = None
        try:
            wav_path = _existing_wav(video, config)
            if wav_path is None:
                stored_source = str(video.get("source_path") or "")
                source_path = Path(stored_source) if stored_source and Path(stored_source).is_file() else None
                if source_path is None:
                    source_path = download_audio(video, config)
                db.update_video(video_id, source_path=str(source_path.resolve()))
                stage = "convert"
                wav_path = convert_to_wav(source_path, config.wav_dir / f"{video_id}.wav", config)
                db.update_video(video_id, status="downloaded", wav_path=str(wav_path.resolve()))
                if not config.keep_downloaded_source:
                    if _cleanup_file(source_path, config.raw_dir, "downloaded source"):
                        db.update_video(video_id, source_path="")

            stage = "vad"
            db.update_video(video_id, status="processing", wav_path=str(wav_path.resolve()))
            if vad is None:
                vad = PyannoteVAD(config)
            speech_regions = vad.detect(wav_path)
            wav_duration = float(probe_wav(wav_path)["duration_sec"])
            speech_regions = [
                SpeechRegion(max(0.0, region.start), min(wav_duration, region.end))
                for region in speech_regions
                if min(wav_duration, region.end) > max(0.0, region.start)
            ]
            planned, dropped_short_seconds = plan_segments(speech_regions, config)
            stage = "extract"
            segment_records = extract_segments(wav_path, video_id, planned, config)
            db.replace_segments(
                video_id,
                segment_records,
                dropped_short_seconds,
                str(wav_path.resolve()),
            )
            if not config.keep_full_wav:
                if _cleanup_file(wav_path, config.wav_dir, "full WAV"):
                    db.update_video(video_id, wav_path="")
            current_seconds = db.valid_seconds()
            elapsed_minutes = (time.perf_counter() - run_started) / 60.0
            print(
                f"[VIDEO] processed id={video_id} segments={len(segment_records)} "
                f"valid_hours={current_seconds / 3600.0:.3f} elapsed={elapsed_minutes:.1f}m",
                flush=True,
            )
        except KeyboardInterrupt:
            resume_status = "downloaded" if wav_path and wav_path.is_file() else "discovered"
            db.update_video(
                video_id,
                status=resume_status,
                attempts=int(video["attempts"]),
                last_error="",
            )
            print("[INTERRUPT] checkpoint is safe; rerun the same command to resume.", flush=True)
            generate_report(config, db)
            raise
        except Exception as exc:
            failure_status = "download_failed" if stage in {"download", "convert"} else "vad_failed"
            error = f"{type(exc).__name__}: {str(exc)[:1500]}"
            db.update_video(video_id, status=failure_status, last_error=error)
            print(f"[ERROR] id={video_id} stage={stage} error={error}", flush=True)

        if position % config.report_every_videos == 0 or current_seconds >= target_seconds:
            generate_report(config, db)
        if current_seconds >= target_seconds:
            print(f"[DONE] target reached: {current_seconds / 3600.0:.3f}h", flush=True)
            break

    return generate_report(config, db)
