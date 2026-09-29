"""Save extended faces and join results to .FCStd and .STEP files."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# isort: off
from surface_assistant import freecad_setup  # noqa: F401
import FreeCAD  # type: ignore
import Part  # type: ignore
# isort: on


def save_extended_faces(report, output_path: Path) -> Path:
    """Save extended faces from a BatchReport to a .FCStd file.

    If the path ends in .step or .stp, the faces are also exported
    as STEP. Otherwise only the FCStd document is written.
    """
    output_path = Path(output_path)
    doc = FreeCAD.newDocument("_SavedOutput")

    for i, face in enumerate(report.extended_faces):
        obj = doc.addObject("Part::Feature", f"Extended_{i}")
        obj.Shape = face

    if report.join_result and report.join_result.sewed_shell is not None:
        joined = doc.addObject("Part::Feature", "JoinedShell")
        joined.Shape = report.join_result.sewed_shell

    doc.recompute()
    doc.saveAs(str(output_path))

    if output_path.suffix.lower() in (".step", ".stp"):
        Part.export(doc.Objects, str(output_path))

    return output_path


def render_fcstd_to_png(fcstd_path, output_png, views=("iso",)):
    """Render one or more FreeCAD views in an isolated GUI process."""
    output_png = Path(output_png)
    if not render_fcstd_to_png_subprocess(Path(fcstd_path), output_png, views=views):
        raise RuntimeError(f"Failed to render screenshot for {fcstd_path}")
    return output_png


def render_fcstd_to_png_subprocess(
    fcstd_path: Path,
    output_png: Path,
    size: int = 1024,
    views: tuple[str, ...] = ("iso",),
) -> bool:
    """Render selected views of an FCStd file in a separate FreeCAD GUI process."""
    fcstd_path = Path(fcstd_path).resolve()
    output_png = Path(output_png).resolve()

    snapshot_script = Path(__file__).resolve().parents[2] / "make_snapshot.py"
    if not snapshot_script.is_file():
        print(f"[render] Snapshot script not found at {snapshot_script}")
        return False

    command = [
        sys.executable,
        str(snapshot_script),
        str(fcstd_path),
        str(output_png),
        "--size",
        str(size),
        "--views",
        ",".join(views),
    ]
    if not os.environ.get("DISPLAY"):
        xvfb_run = shutil.which("xvfb-run")
        if not xvfb_run:
            print("[render] xvfb-run is required when no X display is available")
            return False
        command = [
            xvfb_run,
            "-a",
            "-s",
            "-screen 0 1280x1024x24 +extension GLX",
            *command,
        ]

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "xcb"
    xkb_config = Path("/usr/share/X11/xkb")
    if xkb_config.is_dir():
        env["XKB_CONFIG_ROOT"] = str(xkb_config)

    expected_outputs = (
        [output_png]
        if len(views) == 1
        else [
            output_png.with_name(f"{output_png.stem}_{view}{output_png.suffix}")
            for view in views
        ]
    )

    try:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0 and all(
            path.is_file() and path.stat().st_size > 0 for path in expected_outputs
        ):
            return True
        print(f"[render] Subprocess failed (code {result.returncode})")
        if result.stderr:
            print(f"[render] stderr: {result.stderr[-1000:]}")
        return False

    except subprocess.TimeoutExpired:
        print("[render] Render timed out")
        return False
    except Exception as e:
        print(f"[render] Unexpected error: {e}")
        return False
