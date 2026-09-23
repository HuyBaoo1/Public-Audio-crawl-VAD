from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .utils import utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    webpage_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    upload_date TEXT NOT NULL DEFAULT '',
    duration_sec REAL NOT NULL DEFAULT 0,
    source_url TEXT NOT NULL DEFAULT '',
    selected INTEGER NOT NULL DEFAULT 0,
    filter_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'discovered',
    attempts INTEGER NOT NULL DEFAULT 0,
    source_path TEXT NOT NULL DEFAULT '',
    wav_path TEXT NOT NULL DEFAULT '',
    segment_count INTEGER NOT NULL DEFAULT 0,
    valid_seconds REAL NOT NULL DEFAULT 0,
    dropped_short_seconds REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_work
ON videos(selected, status, attempts, duration_sec DESC);

CREATE TABLE IF NOT EXISTS segments (
    segment_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    audio_path TEXT NOT NULL,
    source_start_sec REAL NOT NULL,
    source_end_sec REAL NOT NULL,
    duration_sec REAL NOT NULL,
    vad_region_index INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    is_hard_split INTEGER NOT NULL,
    sample_rate INTEGER NOT NULL,
    channels INTEGER NOT NULL,
    sample_width_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id);
"""


class PipelineDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PipelineDB":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def set_metadata(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, ensure_ascii=False)
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, serialized),
        )
        self.connection.commit()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def upsert_video(self, item: dict[str, Any]) -> None:
        now = utc_now()
        existing = self.get_video(str(item["video_id"]))
        status = existing["status"] if existing and existing["status"] not in {"filtered", "discovered"} else (
            "discovered" if item["selected"] else "filtered"
        )
        self.connection.execute(
            """
            INSERT INTO videos(
                video_id, webpage_url, title, channel, upload_date, duration_sec,
                source_url, selected, filter_reason, status, metadata_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                webpage_url=excluded.webpage_url,
                title=excluded.title,
                channel=excluded.channel,
                upload_date=excluded.upload_date,
                duration_sec=excluded.duration_sec,
                source_url=excluded.source_url,
                selected=excluded.selected,
                filter_reason=excluded.filter_reason,
                status=CASE
                    WHEN videos.status IN ('processed', 'downloaded', 'processing', 'downloading')
                    THEN videos.status
                    ELSE excluded.status
                END,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                item["video_id"],
                item["webpage_url"],
                item.get("title", ""),
                item.get("channel", ""),
                item.get("upload_date", ""),
                float(item.get("duration_sec") or 0),
                item.get("source_url", ""),
                int(bool(item.get("selected"))),
                item.get("filter_reason", ""),
                status,
                json.dumps(item.get("metadata", {}), ensure_ascii=False),
                now,
                now,
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()

    def selected_videos(self, max_attempts: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT * FROM videos
                WHERE selected=1
                  AND status != 'processed'
                  AND attempts < ?
                ORDER BY duration_sec DESC, upload_date DESC, video_id
                """,
                (max_attempts,),
            )
        )

    def selected_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM videos WHERE selected=1").fetchone()
        return int(row["count"])

    def selected_raw_seconds(self) -> float:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(duration_sec), 0) AS total FROM videos WHERE selected=1"
        ).fetchone()
        return float(row["total"])

    def update_video(self, video_id: str, **values: Any) -> None:
        if not values:
            return
        allowed = {
            "status",
            "attempts",
            "source_path",
            "wav_path",
            "segment_count",
            "valid_seconds",
            "dropped_short_seconds",
            "last_error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported video fields: {sorted(unknown)}")
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        self.connection.execute(
            f"UPDATE videos SET {assignments} WHERE video_id=?",
            (*values.values(), video_id),
        )
        self.connection.commit()

    def increment_attempt(self, video_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE videos SET attempts=attempts+1, status=?, last_error='', updated_at=? WHERE video_id=?",
            (status, utc_now(), video_id),
        )
        self.connection.commit()

    def replace_segments(
        self,
        video_id: str,
        segments: list[dict[str, Any]],
        dropped_short_seconds: float,
        wav_path: str,
    ) -> None:
        valid_seconds = sum(float(item["duration_sec"]) for item in segments)
        now = utc_now()
        with self.transaction() as connection:
            connection.execute("DELETE FROM segments WHERE video_id=?", (video_id,))
            connection.executemany(
                """
                INSERT INTO segments(
                    segment_id, video_id, audio_path, source_start_sec, source_end_sec,
                    duration_sec, vad_region_index, chunk_index, is_hard_split,
                    sample_rate, channels, sample_width_bytes, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["segment_id"],
                        video_id,
                        item["audio_path"],
                        item["source_start_sec"],
                        item["source_end_sec"],
                        item["duration_sec"],
                        item["vad_region_index"],
                        item["chunk_index"],
                        item["is_hard_split"],
                        item["sample_rate"],
                        item["channels"],
                        item["sample_width_bytes"],
                        now,
                    )
                    for item in segments
                ],
            )
            connection.execute(
                """
                UPDATE videos
                SET status='processed', segment_count=?, valid_seconds=?,
                    dropped_short_seconds=?, wav_path=?, last_error='', updated_at=?
                WHERE video_id=?
                """,
                (len(segments), valid_seconds, dropped_short_seconds, wav_path, now, video_id),
            )

    def valid_seconds(self) -> float:
        row = self.connection.execute("SELECT COALESCE(SUM(duration_sec), 0) AS total FROM segments").fetchone()
        return float(row["total"])

    def rows(self, table: str) -> list[sqlite3.Row]:
        if table not in {"videos", "segments"}:
            raise ValueError(f"Unsupported table: {table}")
        order = "video_id" if table == "videos" else "video_id, source_start_sec"
        return list(self.connection.execute(f"SELECT * FROM {table} ORDER BY {order}"))

    def segment_manifest_rows(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT
                    s.*,
                    v.title AS source_title,
                    v.webpage_url AS source_url,
                    v.channel AS source_channel,
                    v.upload_date AS source_upload_date
                FROM segments AS s
                JOIN videos AS v ON v.video_id = s.video_id
                ORDER BY s.video_id, s.source_start_sec
                """
            )
        )

    def iter_segment_manifest(self):
        return self.connection.execute(
            """
            SELECT
                s.*,
                v.title AS source_title,
                v.webpage_url AS source_url,
                v.channel AS source_channel,
                v.upload_date AS source_upload_date
            FROM segments AS s
            JOIN videos AS v ON v.video_id = s.video_id
            ORDER BY s.video_id, s.source_start_sec
            """
        )

    def segment_statistics(self) -> dict[str, float | int]:
        aggregate = self.connection.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(duration_sec), 0) AS total,
                COALESCE(MIN(duration_sec), 0) AS minimum,
                COALESCE(MAX(duration_sec), 0) AS maximum,
                COALESCE(SUM(is_hard_split), 0) AS hard_split_count
            FROM segments
            """
        ).fetchone()
        count = int(aggregate["count"])
        median = 0.0
        if count:
            limit = 2 if count % 2 == 0 else 1
            offset = (count - 1) // 2
            middle = self.connection.execute(
                "SELECT duration_sec FROM segments ORDER BY duration_sec LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            median = sum(float(row["duration_sec"]) for row in middle) / len(middle)
        return {
            "count": count,
            "total": float(aggregate["total"]),
            "minimum": float(aggregate["minimum"]),
            "median": median,
            "maximum": float(aggregate["maximum"]),
            "hard_split_count": int(aggregate["hard_split_count"]),
        }

    def failure_rows(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT * FROM videos
                WHERE last_error != '' OR status IN ('download_failed', 'vad_failed')
                ORDER BY updated_at DESC, video_id
                """
            )
        )

    def summary_counts(self) -> dict[str, int]:
        return {
            row["status"]: int(row["count"])
            for row in self.connection.execute("SELECT status, COUNT(*) AS count FROM videos GROUP BY status")
        }

    def recover_interrupted(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE videos
            SET status='discovered', updated_at=?
            WHERE status IN ('downloading', 'processing')
            """,
            (utc_now(),),
        )
        self.connection.commit()
        return int(cursor.rowcount)
