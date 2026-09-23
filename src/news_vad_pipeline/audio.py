from __future__ import annotations

import math
import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import PipelineConfig


@dataclass(frozen=True)
class SpeechRegion:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def probe_wav(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        compression = handle.getcomptype()
    if frames <= 0 or sample_rate <= 0:
        raise ValueError(f"Empty WAV: {path}")
    if compression != "NONE":
        raise ValueError(f"Compressed WAV is not supported: {path}")
    return {
        "frames": frames,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "duration_sec": frames / sample_rate,
    }


def convert_to_wav(source: Path, destination: Path, config: PipelineConfig) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp.wav")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        str(config.channels),
        "-ar",
        str(config.sample_rate),
        "-c:a",
        "pcm_s16le",
        str(temp),
    ]
    try:
        subprocess.run(command, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg conversion failed: {exc.stderr[-1000:]}") from exc
    info = probe_wav(temp)
    expected = (config.sample_rate, config.channels, config.sample_width_bytes)
    actual = (info["sample_rate"], info["channels"], info["sample_width_bytes"])
    if actual != expected:
        raise RuntimeError(f"Unexpected WAV format {actual}; expected {expected}")
    os.replace(temp, destination)
    return destination


def partition_speech_region(
    start: float,
    end: float,
    min_duration: float,
    max_duration: float,
) -> list[SpeechRegion]:
    duration = end - start
    if duration + 1e-9 < min_duration:
        return []
    if duration <= max_duration + 1e-9:
        return [SpeechRegion(start, end)]

    minimum_parts = math.ceil(duration / max_duration)
    maximum_parts = math.floor(duration / min_duration)
    if minimum_parts > maximum_parts:
        return []
    part_count = minimum_parts
    part_duration = duration / part_count
    regions: list[SpeechRegion] = []
    for index in range(part_count):
        part_start = start + index * part_duration
        part_end = end if index == part_count - 1 else start + (index + 1) * part_duration
        regions.append(SpeechRegion(part_start, part_end))
    return regions


def plan_segments(
    speech_regions: Iterable[SpeechRegion],
    config: PipelineConfig,
) -> tuple[list[dict[str, Any]], float]:
    planned: list[dict[str, Any]] = []
    dropped_short_seconds = 0.0
    for region_index, region in enumerate(speech_regions, start=1):
        chunks = partition_speech_region(
            region.start,
            region.end,
            config.min_segment_sec,
            config.max_segment_sec,
        )
        if not chunks:
            dropped_short_seconds += region.duration
            continue
        hard_split = len(chunks) > 1
        for chunk_index, chunk in enumerate(chunks, start=1):
            planned.append(
                {
                    "start": chunk.start,
                    "end": chunk.end,
                    "vad_region_index": region_index,
                    "chunk_index": chunk_index,
                    "is_hard_split": int(hard_split),
                }
            )
    return planned, dropped_short_seconds


def extract_segments(
    wav_path: Path,
    video_id: str,
    planned: list[dict[str, Any]],
    config: PipelineConfig,
) -> list[dict[str, Any]]:
    output_dir = config.segments_dir / video_id
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with wave.open(str(wav_path), "rb") as source:
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        total_frames = source.getnframes()
        expected = (config.sample_rate, config.channels, config.sample_width_bytes)
        actual = (sample_rate, channels, sample_width)
        if actual != expected:
            raise ValueError(f"Input WAV format {actual} does not match expected {expected}")

        for segment_index, item in enumerate(planned, start=1):
            start_frame = max(0, min(total_frames, round(float(item["start"]) * sample_rate)))
            end_frame = max(start_frame, min(total_frames, round(float(item["end"]) * sample_rate)))
            frame_count = end_frame - start_frame
            duration = frame_count / sample_rate
            if duration + 1e-6 < config.min_segment_sec or duration > config.max_segment_sec + 1e-6:
                raise ValueError(f"Planned segment duration out of range: {duration:.6f}s")
            start_ms = round(start_frame * 1000 / sample_rate)
            end_ms = round(end_frame * 1000 / sample_rate)
            segment_id = f"{video_id}_{segment_index:06d}_{start_ms:010d}_{end_ms:010d}"
            destination = output_dir / f"{segment_id}.wav"
            temp = destination.with_suffix(".tmp.wav")

            source.setpos(start_frame)
            audio_bytes = source.readframes(frame_count)
            with wave.open(str(temp), "wb") as target:
                target.setnchannels(channels)
                target.setsampwidth(sample_width)
                target.setframerate(sample_rate)
                target.writeframes(audio_bytes)
            os.replace(temp, destination)
            records.append(
                {
                    "segment_id": segment_id,
                    "audio_path": str(destination.resolve()),
                    "source_start_sec": start_frame / sample_rate,
                    "source_end_sec": end_frame / sample_rate,
                    "duration_sec": duration,
                    "vad_region_index": int(item["vad_region_index"]),
                    "chunk_index": int(item["chunk_index"]),
                    "is_hard_split": int(item["is_hard_split"]),
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "sample_width_bytes": sample_width,
                }
            )
    return records
