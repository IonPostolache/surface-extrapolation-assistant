"""Save extended faces and join results to .FCStd and .STEP files."""

from __future__ import annotations

from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore


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