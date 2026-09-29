"""Save extended faces and join results to .FCStd and .STEP files."""

from __future__ import annotations

# CRITICAL: This must be set before PySide6 is imported anywhere.
# It tells Qt to render offscreen instead of trying to open a window.
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore

from surface_assistant import freecad_setup  # noqa: F401
import FreeCADGui  # type: ignore

from pivy import coin

# Ensure Qt is initialized
from PySide6 import QtGui  # or PySide6 depending on your FreeCAD build
import sys
import time


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


def render_fcstd_to_png(fcstd_path: Path, output_png: Path) -> Path:
    """Render a FreeCAD document to a PNG image using Qt offscreen mode.

    Uses FreeCAD's real GUI rendering pipeline in offscreen mode. This
    requires a QApplication and FreeCADGui.showMainWindow(), which is
    only valid when setupWithoutGUI() has NOT been called.
    """
    fcstd_path = Path(fcstd_path)
    output_png = Path(output_png)

    if not fcstd_path.exists():
        raise FileNotFoundError(f"FCStd file not found: {fcstd_path}")

    # Import GUI modules lazily so the environment variable is set first.
    import sys
    import FreeCADGui  # type: ignore
    from PySide6 import QtWidgets  # type: ignore

    # Ensure a QApplication exists (offscreen via QT_QPA_PLATFORM).
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    # Bring up FreeCAD's GUI in offscreen mode.
    FreeCADGui.showMainWindow()

    # Open the document through the GUI (creates ViewObjects).
    gui_doc = FreeCADGui.open(str(fcstd_path))
    gui_doc.recompute()

    # Give Qt a moment to process events and build the scene.
    QtWidgets.QApplication.processEvents()

    # Fit the view so the geometry fills the frame.
    try:
        FreeCADGui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    QtWidgets.QApplication.processEvents()

    # Render to PNG.
    view = FreeCADGui.ActiveDocument.ActiveView
    view.saveImage(str(output_png), 1200, 900, "White")

    FreeCADGui.closeDocument(gui_doc.Name)
    return output_png