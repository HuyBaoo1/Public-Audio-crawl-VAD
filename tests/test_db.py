from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from news_vad_pipeline.db import PipelineDB


def video(video_id: str, duration: float) -> dict:
    return {
        "video_id": video_id,
        "webpage_url": f"https://youtube.test/watch?v={video_id}",
        "title": f"Bản tin {video_id}",
        "duration_sec": duration,
        "selected": True,
        "filter_reason": "",
    }


class DatabaseTest(unittest.TestCase):
    def test_long_videos_are_selected_first_and_state_is_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with PipelineDB(Path(temp) / "state.sqlite3") as db:
                db.upsert_video(video("short", 600.0))
                db.upsert_video(video("long", 3600.0))
                db.commit()
                rows = db.selected_videos(max_attempts=3)
                self.assertEqual([row["video_id"] for row in rows], ["long", "short"])
                db.increment_attempt("long", "processing")
                self.assertEqual(db.recover_interrupted(), 1)
                self.assertEqual(db.get_video("long")["status"], "discovered")

    def test_segment_hours_come_from_manifest_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with PipelineDB(Path(temp) / "state.sqlite3") as db:
                db.upsert_video(video("v1", 120.0))
                db.commit()
                db.replace_segments(
                    "v1",
                    [
                        {
                            "segment_id": "v1_1",
                            "audio_path": "/tmp/v1_1.wav",
                            "source_start_sec": 0.0,
                            "source_end_sec": 10.0,
                            "duration_sec": 10.0,
                            "vad_region_index": 1,
                            "chunk_index": 1,
                            "is_hard_split": 0,
                            "sample_rate": 16000,
                            "channels": 1,
                            "sample_width_bytes": 2,
                        }
                    ],
                    dropped_short_seconds=2.0,
                    wav_path="/tmp/v1.wav",
                )
                self.assertAlmostEqual(db.valid_seconds(), 10.0)
                self.assertEqual(db.get_video("v1")["status"], "processed")


if __name__ == "__main__":
    unittest.main()
