# SPDX-License-Identifier: MIT
#
# Render a FreeCAD document to one or more PNG images from the command line.
#


from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from PIL import Image

# isort: off
from surface_assistant import freecad_setup  # noqa: F401  (side-effect import)
import FreeCAD  # type: ignore
import FreeCADGui  # type: ignore
from PySide6 import QtWidgets
# isort: on


# Map a user-facing view name to the FreeCADGui view method that sets it.
# Kept as a plain tuple of pairs so we can report the choices verbatim.
_VIEW_CHOICES: tuple[tuple[str, str], ...] = (
    ("iso", "viewAxonometric"),
    ("front", "viewFront"),
    ("rear", "viewRear"),
    ("top", "viewTop"),
    ("bottom", "viewBottom"),
    ("left", "viewLeft"),
    ("right", "viewRight"),
)
_VIEW_BY_NAME: dict[str, str] = dict(_VIEW_CHOICES)

# Pixels of pure white below which we treat a render as suspiciously empty.
_MIN_NON_WHITE_PIXELS = 100
_MAX_UNIQUE_COLORS = 200_000

# How long to let the event loop settle before grabbing the framebuffer.
_RENDER_SETTLE_SECONDS = 0.1


def _ensure_qt_app() -> QtWidgets.QApplication:
    """Return the process-wide QApplication, creating one if needed."""
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication(sys.argv[:1])


def _normalize_views(raw_views) -> list[str]:
    """Lowercase, strip, and validate the requested view names."""
    normalized = [v.strip().lower() for v in raw_views if v.strip()]
    if not normalized:
        raise ValueError(
            "No views requested. Valid options: "
            + ", ".join(name for name, _ in _VIEW_CHOICES)
        )
    unknown = sorted(set(normalized) - _VIEW_BY_NAME.keys())
    if unknown:
        raise ValueError(
            f"Unknown view(s): {', '.join(unknown)}. "
            f"Valid options: {', '.join(name for name, _ in _VIEW_CHOICES)}"
        )
    return normalized


def _open_document(input_path: Path):
    """Open a FreeCAD document using the appropriate import module."""
    suffix = input_path.suffix.lstrip(".")
    if not suffix:
        raise ValueError(f"Input file has no extension: {input_path}")

    if suffix.lower() == "fcstd":
        return FreeCAD.openDocument(str(input_path))

    importers = FreeCAD.getImportType(suffix)
    if not importers:
        raise ValueError(f"No FreeCAD importer handles '.{suffix}' files")

    importlib.import_module(importers[0]).open(str(input_path))
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError(f"Importer ran but no document is active: {input_path}")
    return doc


def _make_shapes_visible(doc) -> None:
    """If nothing is visible, reveal the shape objects (preferring shells)."""
    shapes = [
        obj for obj in doc.Objects
        if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()
    ]
    if not shapes or any(obj.Visibility for obj in shapes):
        return

    preferred = [obj for obj in shapes if obj.Name == "JoinedShell"]
    to_show = preferred or shapes
    for obj in shapes:
        obj.Visibility = obj in to_show


def _image_looks_blank(path: Path) -> bool:
    """Heuristic: does this PNG consist almost entirely of pure white?"""
    try:
        with Image.open(path) as img:
            histogram = img.getcolors(maxcolors=_MAX_UNIQUE_COLORS)
    except Exception:
        return False

    if histogram is None:
        # More unique colors than we asked for -> definitely not blank.
        return False

    white_pixels = sum(count for count, rgb in histogram if rgb == (255, 255, 255))
    total_pixels = sum(count for count, _ in histogram)
    if total_pixels == 0:
        return True
    return (total_pixels - white_pixels) < _MIN_NON_WHITE_PIXELS


def _settle(app: QtWidgets.QApplication) -> None:
    """Pump the Qt event loop briefly so the GL view finishes painting."""
    app.processEvents()
    import time as _time  # local import keeps module import side-effect free
    _time.sleep(_RENDER_SETTLE_SECONDS)
    app.processEvents()


def _render_one(view, app, destination: Path, size: int) -> None:
    """Save the active 3D view to `destination`, retrying once if blank."""
    view.fitAll()
    _settle(app)
    view.saveImage(str(destination), size, size, "White")

    if not _image_looks_blank(destination):
        return

    # One more attempt with a fresh fit.
    view.fitAll()
    _settle(app)
    view.saveImage(str(destination), size, size, "White")


def snapshot_views(
    input_file,
    output_file,
    size: int = 1024,
    views=("iso",),
):
    """Render `input_file` to PNG image(s) and return the primary output path."""
    input_path = Path(input_file).resolve()
    output_path = Path(output_file).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    view_names = _normalize_views(views)

    app = _ensure_qt_app()
    FreeCADGui.showMainWindow()

    doc = _open_document(input_path)
    try:
        _make_shapes_visible(doc)

        gui_doc = FreeCADGui.ActiveDocument
        if gui_doc is None or gui_doc.ActiveView is None:
            raise RuntimeError("FreeCAD GUI has no active 3D view")
        view = gui_doc.ActiveView

        single_view = len(view_names) == 1
        for name in view_names:
            getattr(view, _VIEW_BY_NAME[name])()

            if single_view:
                target = output_path
            else:
                target = output_path.with_name(
                    f"{output_path.stem}_{name}{output_path.suffix}"
                )

            _render_one(view, app, target, size)

            # Return to a neutral pose before the next view.
            view.viewAxonometric()
            view.fitAll()
            app.processEvents()
    finally:
        FreeCAD.closeDocument(doc.Name)

    return output_path


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a FreeCAD document to PNG image(s).",
    )
    parser.add_argument("input_file", help="FreeCAD or importer-supported CAD file")
    parser.add_argument("output_file", help="Destination PNG path")
    parser.add_argument(
        "--size",
        type=int,
        default=1024,
        help="Image width and height in pixels (default: %(default)s)",
    )
    parser.add_argument(
        "--views",
        default="iso",
        help=(
            "Comma-separated list of views. Choices: "
            + ", ".join(name for name, _ in _VIEW_CHOICES)
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    snapshot_views(
        args.input_file,
        args.output_file,
        size=args.size,
        views=args.views.split(","),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())