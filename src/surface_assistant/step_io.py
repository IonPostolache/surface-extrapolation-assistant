"""STEP file import for headless FreeCAD."""

from __future__ import annotations

from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore


def load_step(step_path: str | Path, doc_name: str = "ImportedSurface"):
    """Import a STEP file into a new FreeCAD document and return its Shape.

    Parameters
    ----------
    step_path : str | Path
        Path to the .step or .stp file.
    doc_name : str
        Name for the new FreeCAD document.

    Returns
    -------
    FreeCAD.Document
        The document containing the imported shape.
    Part.Shape
        The imported shape (B-rep).
    """
    step_path = Path(step_path)
    if not step_path.exists():
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    doc = FreeCAD.newDocument(doc_name)
    Part.insert(str(step_path), doc.Name)

    doc.recompute()

    shapes = [obj.Shape for obj in doc.Objects if hasattr(obj, "Shape")]
    if not shapes:
        raise RuntimeError(f"No shape found after importing {step_path}")

    # If multiple solids/shells were imported, take the first one.
    shape = shapes[0]
    return doc, shape


def load_boundary(boundary_path: str | Path, doc_name: str = "Boundary"):
    """Load a boundary curve from STEP. Returns the wire(s)."""
    doc, shape = load_step(boundary_path, doc_name=doc_name)
    return doc, shape