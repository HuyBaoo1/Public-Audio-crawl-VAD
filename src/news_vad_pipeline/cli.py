from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import shutil
import sys
from pathlib import Path

from .config import load_config
from .db import PipelineDB
from .discovery import discover_videos
from .pipeline import run_pipeline
from .report import generate_report
from .utils import executable_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl Vietnamese YouTube news and segment speech with PyAnnote VAD.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check runtime, FFmpeg, token, and disk.")

    discover = subparsers.add_parser("discover", help="Discover and filter source videos.")
    discover.add_argument("--max-videos-per-source", type=int, default=0)

    run = subparsers.add_parser("run", help="Download, run VAD, segment, and resume until target.")
    run.add_argument("--target-hours", type=float, default=None)
    run.add_argument("--max-videos", type=int, default=0)
    run.add_argument("--discovery-limit", type=int, default=0)

    subparsers.add_parser("report", help="Regenerate EDA_result from SQLite state.")
    return parser


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def _module_exists(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError):
        return False


def doctor(config_path: str) -> int:
    config = load_config(config_path)
    config.ensure_directories()
    checks: list[tuple[str, bool, str]] = []
    python_ok = (3, 10) <= sys.version_info[:2] < (3, 13)
    checks.append(("python", python_ok, sys.version.split()[0]))
    for executable in ["ffmpeg", "ffprobe"]:
        path = executable_path(executable)
        checks.append((executable, bool(path), path or "NOT_FOUND"))
    for package, module in [("yt-dlp", "yt_dlp"), ("pyannote.audio", "pyannote.audio"), ("torch", "torch")]:
        installed = _module_exists(module)
        checks.append((package, installed, _version(package) if installed else "NOT_INSTALLED"))
    token_ok = bool(os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGINGFACE_TOKEN", "").strip())
    checks.append(("HF_TOKEN", token_ok, "SET" if token_ok else "NOT_SET"))
    disk = shutil.disk_usage(config.root)
    pcm_bytes = (
        config.target_hours
        * 3600.0
        * config.sample_rate
        * config.channels
        * config.sample_width_bytes
    )
    storage_multiplier = 2.2 if config.keep_full_wav else 1.2
    if config.keep_downloaded_source:
        storage_multiplier += 0.2
    recommended_bytes = pcm_bytes * storage_multiplier + 5 * 1024**3
    checks.append(
        (
            "disk_free",
            disk.free >= recommended_bytes,
            f"free={disk.free / 1024**3:.1f} GiB recommended={recommended_bytes / 1024**3:.1f} GiB",
        )
    )

    try:
        import torch

        cuda = torch.cuda.is_available()
        detail = torch.cuda.get_device_name(0) if cuda else "CPU_ONLY"
    except ImportError:
        cuda = False
        detail = "TORCH_NOT_INSTALLED"
    requested_cuda = config.vad_device == "cuda"
    checks.append(("cuda", cuda or not requested_cuda, detail))

    failed = False
    for name, passed, detail in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {name}: {detail}")
        failed = failed or not passed
    print(f"[INFO] config={config.config_path}")
    print(f"[INFO] target_hours={config.target_hours}")
    print(f"[INFO] database={config.database_path}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor(args.config)

    config = load_config(args.config)
    config.ensure_directories()
    with PipelineDB(config.database_path) as db:
        if args.command == "discover":
            discover_videos(config, db, max_videos_per_source=args.max_videos_per_source)
            generate_report(config, db)
            return 0
        if args.command == "run":
            summary = run_pipeline(
                config,
                db,
                target_hours=args.target_hours,
                max_videos=args.max_videos,
                discovery_limit=args.discovery_limit,
            )
            return 0 if summary["target_reached"] else 2
        if args.command == "report":
            generate_report(config, db)
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
