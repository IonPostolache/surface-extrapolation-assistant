# SPDX-License-Identifier: LGPL-2.1-or-later

# (c) 2022 Werner Mayer LGPL

"""Save a FreeCAD document view as a PNG image."""
import argparse
import importlib
import sys
from pathlib import Path

# isort: off
from surface_assistant import freecad_setup  # noqa: F401
import FreeCAD  # type: ignore
import FreeCADGui  # type: ignore
from PySide6 import QtWidgets
# isort: on


VIEW_METHODS = {
    "iso": "viewAxonometric",
    "front": "viewFront",
    "top": "viewTop",
    "left": "viewLeft",
}


def make_snapshot(input_file, output_file, size=1024, views=("iso",)):
    input_file = Path(input_file).resolve()
    output_file = Path(output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    views = tuple(view.strip().lower() for view in views)
    unknown_views = set(views) - VIEW_METHODS.keys()
    if not views or unknown_views:
        raise ValueError(
            f"Unsupported views: {sorted(unknown_views)}. "
            f"Choose from: {', '.join(VIEW_METHODS)}"
        )

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    FreeCADGui.showMainWindow()

    ext = input_file.suffix[1:]
    mod = FreeCAD.getImportType(ext)
    if len(mod) == 0:
        raise ValueError(f"Cannot load file: {input_file}")

    if ext.lower() == "fcstd":
        doc = FreeCAD.openDocument(str(input_file))
    else:
        module = importlib.import_module(mod[0])
        module.open(str(input_file))
        doc = FreeCAD.ActiveDocument

    if doc is None:
        raise RuntimeError(f"FreeCAD did not open a document: {input_file}")

    try:
        shape_objects = [
            obj for obj in doc.Objects
            if hasattr(obj, "Shape") and not obj.Shape.isNull()
        ]
        if not any(obj.Visibility for obj in shape_objects):
            joined_shells = [obj for obj in shape_objects if obj.Name == "JoinedShell"]
            visible_objects = joined_shells or shape_objects
            for obj in shape_objects:
                obj.Visibility = obj in visible_objects

        view = FreeCADGui.ActiveDocument.ActiveView
        for view_name in views:
            getattr(view, VIEW_METHODS[view_name])()
            view.fitAll()
            app.processEvents()
            if len(views) == 1:
                view_path = output_file
            else:
                view_path = output_file.with_name(
                    f"{output_file.stem}_{view_name}{output_file.suffix}"
                )
            view.saveImage(str(view_path), size, size, "White")
    finally:
        FreeCAD.closeDocument(doc.Name)

    return output_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", help="FreeCAD document or supported CAD file")
    parser.add_argument("output_file", help="Destination PNG path")
    parser.add_argument("--size", type=int, default=1024, help="Image width and height in pixels")
    parser.add_argument(
        "--views",
        default="iso",
        help="Comma-separated views: iso, front, top, left",
    )
    args = parser.parse_args()
    make_snapshot(
        args.input_file,
        args.output_file,
        args.size,
        views=args.views.split(","),
    )
