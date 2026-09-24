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
from .pipeline import crawl_audio, run_pipeline, run_vad_only
from .report import generate_report
from .transfer import export_crawl, import_crawl
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

    doctor_parser = subparsers.add_parser("doctor", help="Check dependencies for one machine stage.")
    doctor_parser.add_argument("--stage", choices=["all", "crawl", "vad"], default="all")

    discover = subparsers.add_parser("discover", help="Discover and filter source videos.")
    discover.add_argument("--max-videos-per-source", type=int, default=0)

    crawl = subparsers.add_parser("crawl", help="Download audio only; do not run FFmpeg or VAD.")
    crawl.add_argument("--target-source-hours", type=float, default=None)
    crawl.add_argument("--max-videos", type=int, default=0)
    crawl.add_argument("--discovery-limit", type=int, default=0)

    subparsers.add_parser("export-crawl", help="Create portable crawl manifest and checksums.")

    import_parser = subparsers.add_parser(
        "import-crawl", help="Verify transferred audio and import it into the HPC database."
    )
    import_parser.add_argument("--manifest", default=None)
    import_parser.add_argument("--skip-hash-check", action="store_true")

    vad = subparsers.add_parser("vad", help="Run FFmpeg and VAD on transferred local audio only.")
    vad.add_argument("--target-hours", type=float, default=None)
    vad.add_argument("--max-videos", type=int, default=0)

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


def doctor(config_path: str, stage: str = "all") -> int:
    config = load_config(config_path)
    config.ensure_directories()
    checks: list[tuple[str, bool, str]] = []
    python_ok = (3, 10) <= sys.version_info[:2] < (3, 13)
    checks.append(("python", python_ok, sys.version.split()[0]))
    if stage in {"all", "crawl"}:
        installed = _module_exists("yt_dlp")
        checks.append(("yt-dlp", installed, _version("yt-dlp") if installed else "NOT_INSTALLED"))
        ejs_version = _version("yt-dlp-ejs")
        checks.append(("yt-dlp-ejs", ejs_version != "NOT_INSTALLED", ejs_version))
        deno = executable_path("deno")
        checks.append(("deno", bool(deno), deno or "NOT_FOUND"))
    if stage in {"all", "vad"}:
        for executable in ["ffmpeg", "ffprobe"]:
            path = executable_path(executable)
            checks.append((executable, bool(path), path or "NOT_FOUND"))
        for package, module in [("pyannote.audio", "pyannote.audio"), ("torch", "torch")]:
            installed = _module_exists(module)
            checks.append((package, installed, _version(package) if installed else "NOT_INSTALLED"))
        token_ok = bool(
            os.environ.get("HF_TOKEN", "").strip()
            or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
        )
        checks.append(("HF_TOKEN", token_ok, "SET" if token_ok else "NOT_SET"))
    disk = shutil.disk_usage(config.root)
    if stage == "crawl":
        recommended_bytes = config.target_hours * 1.5 * 80 * 1024**2 + 2 * 1024**3
    else:
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

    if stage in {"all", "vad"}:
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
    print(f"[INFO] stage={stage}")
    print(f"[INFO] target_hours={config.target_hours}")
    print(f"[INFO] database={config.database_path}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor(args.config, args.stage)

    config = load_config(args.config)
    config.ensure_directories()
    with PipelineDB(config.database_path) as db:
        if args.command == "discover":
            discover_videos(config, db, max_videos_per_source=args.max_videos_per_source)
            generate_report(config, db)
            return 0
        if args.command == "crawl":
            summary = crawl_audio(
                config,
                db,
                target_source_hours=args.target_source_hours,
                max_videos=args.max_videos,
                discovery_limit=args.discovery_limit,
            )
            return 0 if summary["target_reached"] else 2
        if args.command == "export-crawl":
            export_crawl(config, db)
            return 0
        if args.command == "import-crawl":
            manifest = Path(args.manifest).expanduser() if args.manifest else None
            import_crawl(
                config,
                db,
                manifest_path=manifest,
                verify_hash=not args.skip_hash_check,
            )
            return 0
        if args.command == "vad":
            summary = run_vad_only(
                config,
                db,
                target_hours=args.target_hours,
                max_videos=args.max_videos,
            )
            return 0 if summary["target_reached"] else 2
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
