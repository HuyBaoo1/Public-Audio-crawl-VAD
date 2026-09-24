from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from news_vad_pipeline.config import load_config
from news_vad_pipeline.db import PipelineDB
from news_vad_pipeline.pipeline import run_vad_only


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VadOnlyTest(unittest.TestCase):
    def test_missing_local_audio_never_falls_back_to_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = load_config(PROJECT_ROOT / "config.toml")
            config = replace(
                base,
                root=root,
                raw_dir=root / "data" / "raw",
                wav_dir=root / "data" / "wav",
                segments_dir=root / "data" / "segments",
                state_dir=root / "state",
                result_dir=root / "EDA_result",
                hf_cache_dir=root / "hf_cache",
                transfer_dir=root / "transfer",
            )
            with PipelineDB(config.database_path) as db:
                db.upsert_video(
                    {
                        "video_id": "missing",
                        "webpage_url": "https://youtube.test/watch?v=missing",
                        "title": "Bản tin thiếu file",
                        "duration_sec": 600.0,
                        "selected": True,
                        "filter_reason": "",
                    }
                )
                db.commit()
                db.update_video("missing", status="downloaded")
                with patch(
                    "news_vad_pipeline.pipeline.download_audio",
                    side_effect=AssertionError("network download must not be called"),
                ) as downloader:
                    summary = run_vad_only(config, db, target_hours=0.1)
                downloader.assert_not_called()
                self.assertFalse(summary["target_reached"])
                row = db.get_video("missing")
                self.assertEqual(row["status"], "vad_failed")
                self.assertIn("will not download", row["last_error"])


if __name__ == "__main__":
    unittest.main()
