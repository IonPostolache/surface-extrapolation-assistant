"""STEP file import for headless FreeCAD."""

from __future__ import annotations

from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore


def _collect_shapes(doc) -> list:
    """Return all Part.Shape objects found in the document."""
    shapes = []
    for obj in doc.Objects:
        if hasattr(obj, "Shape") and obj.Shape is not None:
            shapes.append(obj.Shape)
    return shapes


def load_step(step_path: str | Path, doc_name: str = "ImportedSurface"):
    """Import a STEP file and return (doc, shape).

    The returned shape is a compound merging all imported objects,
    so callers see every face at once even if the STEP file contains
    multiple objects.

    Raises RuntimeError if the imported shape has no faces.
    """
    step_path = Path(step_path)
    if not step_path.exists():
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    doc = FreeCAD.newDocument(doc_name)
    Part.insert(str(step_path), doc.Name)
    doc.recompute()

    shapes = _collect_shapes(doc)
    if not shapes:
        raise RuntimeError(f"No shape found after importing {step_path}")

    shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)

    if len(shape.Faces) == 0:
        raise RuntimeError(
            f"Imported shape from {step_path} contains no faces."
        )

    return doc, shape


def load_boundary(boundary_path: str | Path, doc_name: str = "Boundary"):
    """Load a boundary curve from STEP. Returns (doc, shape).

    Boundary shapes contain edges/wires rather than faces, so the
    validation here accepts edges instead of faces.
    """
    boundary_path = Path(boundary_path)
    if not boundary_path.exists():
        raise FileNotFoundError(f"Boundary file not found: {boundary_path}")

    doc = FreeCAD.newDocument(doc_name)
    Part.insert(str(boundary_path), doc.Name)
    doc.recompute()

    shapes = _collect_shapes(doc)
    if not shapes:
        raise RuntimeError(
            f"No shape found after importing boundary {boundary_path}"
        )

    shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)

    if len(shape.Edges) == 0:
        raise RuntimeError(
            f"Boundary shape from {boundary_path} contains no edges."
        )

    return doc, shape