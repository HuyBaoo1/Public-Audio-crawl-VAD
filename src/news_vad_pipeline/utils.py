from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", without_marks).strip()


def keyword_match(title: str, keywords: tuple[str, ...]) -> bool:
    normalized_title = normalize_text(title)
    return any(normalize_text(keyword) in normalized_title for keyword in keywords if keyword.strip())


def atomic_write_text(path: Path, content: str) -> None:
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
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def executable_path(name: str) -> str:
    return shutil.which(name) or ""


def run_command(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def safe_unlink(path: Path, allowed_parent: Path) -> bool:
    if not path.exists():
        return False
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if parent != resolved.parent and parent not in resolved.parents:
        raise ValueError(f"Refusing to remove path outside {parent}: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Refusing to remove non-file path: {resolved}")
    resolved.unlink()
    return True
