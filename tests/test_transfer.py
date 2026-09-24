from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from news_vad_pipeline.config import load_config
from news_vad_pipeline.db import PipelineDB
from news_vad_pipeline.transfer import export_crawl, import_crawl


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TransferTest(unittest.TestCase):
    def test_export_and_import_are_portable_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            personal = root / "personal"
            hpc = root / "hpc"
            personal_raw = personal / "data" / "raw"
            personal_transfer = personal / "transfer"
            personal_raw.mkdir(parents=True)
            audio = personal_raw / "video1.webm"
            audio.write_bytes(b"portable-audio-fixture")

            base = load_config(PROJECT_ROOT / "config.toml")
            personal_config = replace(
                base,
                root=personal,
                raw_dir=personal_raw,
                transfer_dir=personal_transfer,
            )
            with PipelineDB(personal / "state.sqlite3") as db:
                db.upsert_video(
                    {
                        "video_id": "video1",
                        "webpage_url": "https://youtube.test/watch?v=video1",
                        "title": "Bản tin thử nghiệm",
                        "channel": "Test channel",
                        "upload_date": "20260924",
                        "duration_sec": 3600.0,
                        "source_url": "https://youtube.test/channel",
                        "selected": True,
                        "filter_reason": "",
                    }
                )
                db.commit()
                db.update_video("video1", status="downloaded", source_path=str(audio))
                summary = export_crawl(personal_config, db)
                self.assertEqual(summary["video_count"], 1)

            hpc_raw = hpc / "data" / "raw"
            hpc_transfer = hpc / "transfer"
            hpc_raw.mkdir(parents=True)
            shutil.copy2(audio, hpc_raw / audio.name)
            shutil.copytree(personal_transfer, hpc_transfer)
            hpc_config = replace(
                base,
                root=hpc,
                raw_dir=hpc_raw,
                transfer_dir=hpc_transfer,
            )
            with PipelineDB(hpc / "state.sqlite3") as db:
                result = import_crawl(hpc_config, db)
                self.assertEqual(result["imported"], 1)
                imported = db.get_video("video1")
                self.assertEqual(imported["status"], "downloaded")
                self.assertEqual(Path(imported["source_path"]), hpc_raw / audio.name)


if __name__ == "__main__":
    unittest.main()
