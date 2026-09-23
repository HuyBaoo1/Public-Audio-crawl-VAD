from __future__ import annotations

import math
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path

from news_vad_pipeline.audio import SpeechRegion, extract_segments, partition_speech_region, plan_segments
from news_vad_pipeline.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PartitionTest(unittest.TestCase):
    def test_short_region_is_dropped(self) -> None:
        self.assertEqual(partition_speech_region(0.0, 4.999, 5.0, 30.0), [])

    def test_region_inside_bounds_is_unchanged(self) -> None:
        regions = partition_speech_region(2.0, 22.0, 5.0, 30.0)
        self.assertEqual(regions, [SpeechRegion(2.0, 22.0)])

    def test_31_seconds_is_balanced_without_short_tail(self) -> None:
        regions = partition_speech_region(0.0, 31.0, 5.0, 30.0)
        self.assertEqual(len(regions), 2)
        self.assertAlmostEqual(regions[0].duration, 15.5)
        self.assertAlmostEqual(regions[1].duration, 15.5)

    def test_long_region_preserves_duration_and_bounds(self) -> None:
        regions = partition_speech_region(10.0, 110.0, 5.0, 30.0)
        self.assertEqual(len(regions), 4)
        self.assertAlmostEqual(sum(item.duration for item in regions), 100.0)
        self.assertTrue(all(5.0 <= item.duration <= 30.0 for item in regions))


class ExtractionTest(unittest.TestCase):
    def _write_silence(self, path: Path, duration: float, sample_rate: int = 16000) -> None:
        frame_count = round(duration * sample_rate)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(b"\x00\x00" * frame_count)

    def test_planned_segments_are_valid_wav_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "source.wav"
            self._write_silence(source, 40.0)
            config = replace(
                load_config(PROJECT_ROOT / "config.toml"),
                segments_dir=temp_path / "segments",
            )
            planned, dropped = plan_segments(
                [SpeechRegion(0.0, 31.0), SpeechRegion(32.0, 36.0)],
                config,
            )
            self.assertAlmostEqual(dropped, 4.0)
            records = extract_segments(source, "video123", planned, config)
            self.assertEqual(len(records), 2)
            for record in records:
                self.assertTrue(Path(record["audio_path"]).is_file())
                self.assertGreaterEqual(record["duration_sec"], 5.0)
                self.assertLessEqual(record["duration_sec"], 30.0)
                self.assertEqual(record["is_hard_split"], 1)


if __name__ == "__main__":
    unittest.main()
