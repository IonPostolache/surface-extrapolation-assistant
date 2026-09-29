"""FreeCAD path bootstrap.

FreeCAD is not pip-installable. It ships its own Python interpreter and
C++ extension modules. To use FreeCAD from a venv, we must append its
library directory to sys.path BEFORE importing FreeCAD.

Import this module first:

    from surface_assistant import freecad_setup  # noqa: F401
    import FreeCAD
    import Part
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from surface_assistant.config import load_config


def _load_config_path() -> str | None:
    try:
        cfg = load_config()
        return cfg.freecad.lib_path or None
    except Exception:
        return None


def _candidate_paths() -> list[str]:
    """Common FreeCAD install locations per OS."""
    candidates: list[str] = []

    if sys.platform.startswith("win"):
        for version in ("1.0", "0.21", "0.20"):
            candidates.append(rf"C:\Program Files\FreeCAD {version}\bin")
            candidates.append(rf"C:\Program Files\FreeCAD {version}\lib")
    elif sys.platform == "darwin":
        candidates.extend([
            "/Applications/FreeCAD.app/Contents/Resources/lib",
            "/Applications/FreeCAD.app/Contents/lib",
            "/opt/homebrew/lib/freecad/lib",
        ])
    else:  # Linux
        candidates.extend([
            "/usr/lib/freecad/lib",
            "/usr/lib/freecad-python3/lib",
            "/usr/local/lib/freecad/lib",
            "/snap/freecad/current/usr/lib/freecad/lib",
        ])

    # Environment variable override
    env_path = os.environ.get("FREECAD_LIB_PATH")
    if env_path:
        candidates.insert(0, env_path)

    return candidates


def setup_freecad_path() -> str:
    """Add FreeCAD's lib directory to sys.path.

    Returns the resolved path that was added.
    Raises RuntimeError if FreeCAD cannot be located.
    """
    # 1. Try config.yaml
    cfg_path = _load_config_path()
    if cfg_path and Path(cfg_path).exists():
        if cfg_path not in sys.path:
            sys.path.append(cfg_path)
        return cfg_path

    # 2. Try common locations
    for candidate in _candidate_paths():
        if Path(candidate).exists():
            if candidate not in sys.path:
                sys.path.append(candidate)
            return candidate

    raise RuntimeError(
        "FreeCAD library path not found.\n"
        "Set 'freecad.lib_path' in config.yaml, or set the "
        "FREECAD_LIB_PATH environment variable to your FreeCAD bin/ or lib/ folder."
    )


# Auto-run on import
RESOLVED_FREECAD_PATH = setup_freecad_path()