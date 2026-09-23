from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .config import PipelineConfig
from .db import PipelineDB
from .utils import atomic_write_json, atomic_write_text, utc_now


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _dict_rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def build_summary(config: PipelineConfig, db: PipelineDB) -> dict[str, Any]:
    videos = _dict_rows(db.rows("videos"))
    segment_stats = db.segment_statistics()
    target_hours = float(db.get_metadata("target_hours", config.target_hours))
    valid_seconds = float(segment_stats["total"])
    status_counts = Counter(str(row["status"]) for row in videos)
    filter_counts = Counter(str(row["filter_reason"]) for row in videos if row["filter_reason"])
    selected = [row for row in videos if int(row["selected"]) == 1]
    processed = [row for row in videos if row["status"] == "processed"]
    return {
        "generated_at_utc": utc_now(),
        "target_hours": target_hours,
        "target_reached": valid_seconds >= target_hours * 3600.0,
        "completion_percent": min(100.0, valid_seconds / max(1.0, target_hours * 3600.0) * 100.0),
        "valid_segment_hours": valid_seconds / 3600.0,
        "segment_count": int(segment_stats["count"]),
        "segment_duration_sec": {
            "min": float(segment_stats["minimum"]),
            "median": float(segment_stats["median"]),
            "max": float(segment_stats["maximum"]),
        },
        "hard_split_segment_count": int(segment_stats["hard_split_count"]),
        "discovered_video_count": len(videos),
        "selected_video_count": len(selected),
        "selected_raw_hours": sum(float(row["duration_sec"]) for row in selected) / 3600.0,
        "processed_video_count": len(processed),
        "processed_source_hours": sum(float(row["duration_sec"]) for row in processed) / 3600.0,
        "dropped_short_speech_hours": sum(float(row["dropped_short_seconds"]) for row in processed) / 3600.0,
        "video_status_counts": dict(status_counts),
        "filter_reason_counts": dict(filter_counts),
        "failure_count": len(db.failure_rows()),
        "paths": {
            "segments_dir": str(config.segments_dir),
            "database": str(config.database_path),
            "result_dir": str(config.result_dir),
        },
        "vad": {
            "model_id": config.vad_model_id,
            "device": config.vad_device,
            "min_duration_on": config.min_duration_on,
            "min_duration_off": config.min_duration_off,
        },
        "audio": {
            "sample_rate": config.sample_rate,
            "channels": config.channels,
            "sample_width_bytes": config.sample_width_bytes,
            "min_segment_sec": config.min_segment_sec,
            "max_segment_sec": config.max_segment_sec,
        },
    }


def _report_markdown(summary: dict[str, Any]) -> str:
    reached = "ĐẠT" if summary["target_reached"] else "CHƯA ĐẠT"
    duration = summary["segment_duration_sec"]
    status_json = json.dumps(summary["video_status_counts"], ensure_ascii=False)
    lines = [
        "# Báo cáo YouTube News + PyAnnote VAD",
        "",
        "## Tiến độ",
        "",
        "| Chỉ số | Kết quả |",
        "|---|---:|",
        f"| Mục tiêu | {summary['target_hours']:.1f} giờ |",
        f"| Audio segment hợp lệ | {summary['valid_segment_hours']:.3f} giờ |",
        f"| Hoàn thành | {summary['completion_percent']:.2f}% |",
        f"| Trạng thái | **{reached}** |",
        "",
        "## Dataset",
        "",
        "| Chỉ số | Kết quả |",
        "|---|---:|",
        f"| Video phát hiện / được chọn | {summary['discovered_video_count']:,} / {summary['selected_video_count']:,} |",
        f"| Video đã xử lý | {summary['processed_video_count']:,} |",
        f"| Tổng segment | {summary['segment_count']:,} |",
        f"| Segment min / median / max | {duration['min']:.3f}s / {duration['median']:.3f}s / {duration['max']:.3f}s |",
        f"| Segment từ hard-split vùng speech >30s | {summary['hard_split_segment_count']:,} |",
        f"| Speech <5s bị loại | {summary['dropped_short_speech_hours']:.3f} giờ |",
        f"| Video lỗi cần retry | {summary['failure_count']:,} |",
        "",
        "## Trạng thái video",
        "",
        f"`{status_json}`",
        "",
        "## Output",
        "",
        f"- Segment audio: `{summary['paths']['segments_dir']}`",
        "- Manifest: `segments.csv`",
        "- Video state: `videos.csv`",
        "- Lỗi: `failures.csv`",
        "- Thống kê máy đọc: `summary.json`",
        "",
        "Các vùng speech ngắn hơn 5 giây bị loại. Vùng speech dài hơn 30 giây được chia đều thành các đoạn 5-30 giây; điểm chia có thể nằm giữa lời nói theo yêu cầu hiện tại.",
    ]
    return "\n".join(lines) + "\n"


def generate_report(config: PipelineConfig, db: PipelineDB) -> dict[str, Any]:
    config.result_dir.mkdir(parents=True, exist_ok=True)
    videos = _dict_rows(db.rows("videos"))
    failures = _dict_rows(db.failure_rows())
    video_fields = list(videos[0].keys()) if videos else ["video_id"]
    segment_fields = [
        "segment_id",
        "video_id",
        "audio_path",
        "source_start_sec",
        "source_end_sec",
        "duration_sec",
        "vad_region_index",
        "chunk_index",
        "is_hard_split",
        "sample_rate",
        "channels",
        "sample_width_bytes",
        "created_at",
        "source_title",
        "source_url",
        "source_channel",
        "source_upload_date",
    ]
    failure_fields = list(failures[0].keys()) if failures else video_fields
    _write_csv(config.result_dir / "videos.csv", videos, video_fields)
    _write_csv(
        config.result_dir / "segments.csv",
        (dict(row) for row in db.iter_segment_manifest()),
        segment_fields,
    )
    _write_csv(config.result_dir / "failures.csv", failures, failure_fields)
    summary = build_summary(config, db)
    atomic_write_json(config.result_dir / "summary.json", summary)
    atomic_write_text(config.result_dir / "REPORT.md", _report_markdown(summary))
    print(
        f"[REPORT] valid_hours={summary['valid_segment_hours']:.3f} "
        f"segments={summary['segment_count']} target_reached={summary['target_reached']} "
        f"dir={config.result_dir}",
        flush=True,
    )
    return summary
