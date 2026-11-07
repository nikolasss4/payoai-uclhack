"""Loads each process's own env files, so a process only holds the secrets it needs."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env(*names: str) -> None:
    for name in names:
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def schema() -> str:
    return os.environ.get("PAYOAI_SCHEMA", "payoai")
