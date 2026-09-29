"""Load and provide access to config.yaml settings.

Precedence:
    1. CLI arguments (highest)
    2. config.local.yaml (if present)
    3. config.yaml
    4. Built-in defaults (lowest)

The config file is located relative to the project root (two levels up
from this module).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    # __file__ = .../src/surface_assistant/config.py
    return Path(__file__).resolve().parents[2]


def _config_candidates() -> list[Path]:
    root = _project_root()
    return [
        root / "config.local.yaml",  # machine-specific overrides
        root / "config.yaml",        # committed defaults
    ]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

@dataclass
class FreeCADConfig:
    lib_path: str = ""


@dataclass
class ExtrapolationConfig:
    target_distance_mm: float = 100.0
    tolerance_percent: float = 2.0
    max_correction_passes: int = 1
    initial_ratio: float = 1.0


@dataclass
class JoinConfig:
    sewing_tolerance_mm: float = 0.04
    refine_shape: bool = True


@dataclass
class LLMConfig:
    enabled: bool = True
    provider: str = "ollama"
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:7b"
    timeout_seconds: int = 60
    temperature: float = 0.2


@dataclass
class AppConfig:
    freecad: FreeCADConfig
    extrapolation: ExtrapolationConfig
    join: JoinConfig
    llm: LLMConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_yaml() -> dict:
    """Merge config.local.yaml over config.yaml (local wins)."""
    merged: dict = {}
    for path in _config_candidates():
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _deep_merge(merged, data)
        except Exception:
            continue
    return merged


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_config() -> AppConfig:
    """Read config.yaml (and config.local.yaml) and return an AppConfig."""
    data = _load_yaml()

    fc = data.get("freecad", {}) or {}
    ex = data.get("extrapolation", {}) or {}
    jn = data.get("join", {}) or {}
    lm = data.get("llm", {}) or {}

    return AppConfig(
        freecad=FreeCADConfig(
            lib_path=fc.get("lib_path", "") or "",
        ),
        extrapolation=ExtrapolationConfig(
            target_distance_mm=float(ex.get("target_distance_mm", 100.0)),
            tolerance_percent=float(ex.get("tolerance_percent", 2.0)),
            max_correction_passes=int(ex.get("max_correction_passes", 1)),
            initial_ratio=float(ex.get("initial_ratio", 1.0)),
        ),
        join=JoinConfig(
            sewing_tolerance_mm=float(jn.get("sewing_tolerance_mm", 0.04)),
            refine_shape=bool(jn.get("refine_shape", True)),
        ),
        llm=LLMConfig(
            enabled=bool(lm.get("enabled", True)),
            provider=str(lm.get("provider", "ollama")),
            endpoint=str(lm.get("endpoint", "http://localhost:11434")),
            model=str(lm.get("model", "qwen2.5-coder:7b")),
            timeout_seconds=int(lm.get("timeout_seconds", 60)),
            temperature=float(lm.get("temperature", 0.2)),
        ),
    )