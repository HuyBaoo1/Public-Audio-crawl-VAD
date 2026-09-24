from __future__ import annotations

import csv
import hashlib
import hmac
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .db import PipelineDB
from .downloader import find_downloaded_audio
from .utils import atomic_write_json, atomic_write_text, utc_now


MANIFEST_FIELDS = [
    "video_id",
    "audio_file",
    "size_bytes",
    "sha256",
    "duration_sec",
    "webpage_url",
    "title",
    "channel",
    "upload_date",
    "source_url",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
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
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def export_crawl(config: PipelineConfig, db: PipelineDB) -> dict[str, Any]:
    config.transfer_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in db.downloaded_rows():
        video = dict(row)
        audio_path = find_downloaded_audio(config.raw_dir, str(video["video_id"]))
        if audio_path is None:
            missing.append(str(video["video_id"]))
            continue
        print(f"[EXPORT] hashing={audio_path.name}", flush=True)
        manifest_rows.append(
            {
                "video_id": video["video_id"],
                "audio_file": audio_path.name,
                "size_bytes": audio_path.stat().st_size,
                "sha256": sha256_file(audio_path),
                "duration_sec": video["duration_sec"],
                "webpage_url": video["webpage_url"],
                "title": video["title"],
                "channel": video["channel"],
                "upload_date": video["upload_date"],
                "source_url": video["source_url"],
            }
        )
    if missing:
        preview = ", ".join(missing[:10])
        raise RuntimeError(
            f"{len(missing)} downloaded database rows have no local audio file. "
            f"First IDs: {preview}"
        )

    manifest_path = config.transfer_dir / "crawl_manifest.csv"
    _write_manifest(manifest_path, manifest_rows)
    source_hours = sum(float(row["duration_sec"]) for row in manifest_rows) / 3600.0
    total_bytes = sum(int(row["size_bytes"]) for row in manifest_rows)
    summary = {
        "generated_at_utc": utc_now(),
        "video_count": len(manifest_rows),
        "source_hours": source_hours,
        "total_bytes": total_bytes,
        "raw_dir": str(config.raw_dir),
        "manifest": str(manifest_path),
    }
    atomic_write_json(config.transfer_dir / "crawl_summary.json", summary)
    checksums = "".join(
        f"{row['sha256']}  data/raw/{row['audio_file']}\n" for row in manifest_rows
    )
    atomic_write_text(config.transfer_dir / "SHA256SUMS", checksums)
    print(
        f"[EXPORT] videos={len(manifest_rows)} source_hours={source_hours:.3f} "
        f"size_gib={total_bytes / 1024**3:.2f} dir={config.transfer_dir}",
        flush=True,
    )
    return summary


def import_crawl(
    config: PipelineConfig,
    db: PipelineDB,
    *,
    manifest_path: Path | None = None,
    verify_hash: bool = True,
) -> dict[str, Any]:
    manifest = (manifest_path or config.transfer_dir / "crawl_manifest.csv").resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f"Crawl manifest not found: {manifest}")
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    validated: list[tuple[dict[str, str], Path]] = []
    for row in rows:
        video_id = str(row.get("video_id") or "").strip()
        audio_file = str(row.get("audio_file") or "").strip()
        if not video_id or not audio_file or Path(audio_file).name != audio_file:
            raise ValueError(f"Unsafe or incomplete manifest row: video_id={video_id!r}")
        if not audio_file.startswith(f"{video_id}."):
            raise ValueError(f"Audio filename does not match video ID {video_id}: {audio_file}")
        audio_path = (config.raw_dir / audio_file).resolve()
        if audio_path.parent != config.raw_dir.resolve() or not audio_path.is_file():
            raise FileNotFoundError(f"Transferred audio is missing: {audio_path}")
        expected_size = int(row["size_bytes"])
        if audio_path.stat().st_size != expected_size:
            raise ValueError(f"Size mismatch for {audio_file}")
        if verify_hash:
            print(f"[IMPORT] verifying={audio_file}", flush=True)
            actual_hash = sha256_file(audio_path)
            if not hmac.compare_digest(actual_hash, str(row["sha256"]).casefold()):
                raise ValueError(f"SHA-256 mismatch for {audio_file}")
        validated.append((row, audio_path))

    imported = 0
    already_processed = 0
    for row, audio_path in validated:
        video_id = str(row["video_id"])
        db.upsert_video(
            {
                "video_id": video_id,
                "webpage_url": row["webpage_url"],
                "title": row["title"],
                "channel": row["channel"],
                "upload_date": row["upload_date"],
                "duration_sec": float(row["duration_sec"]),
                "source_url": row["source_url"],
                "selected": True,
                "filter_reason": "",
                "metadata": {"imported_from": str(manifest)},
            }
        )
        current = db.get_video(video_id)
        if current and current["status"] == "processed":
            already_processed += 1
            continue
        db.update_video(
            video_id,
            status="downloaded",
            source_path=str(audio_path),
            last_error="",
        )
        imported += 1
    db.commit()
    result = {
        "manifest": str(manifest),
        "verified": verify_hash,
        "manifest_rows": len(rows),
        "imported": imported,
        "already_processed": already_processed,
        "source_hours": sum(float(row["duration_sec"]) for row in rows) / 3600.0,
    }
    print(
        f"[IMPORT] rows={len(rows)} imported={imported} "
        f"already_processed={already_processed} verified={verify_hash}",
        flush=True,
    )
    return result
