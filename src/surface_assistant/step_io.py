"""STEP file import for headless FreeCAD."""

from __future__ import annotations

from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore


def _resolve_step_path(path: str | Path) -> Path:
    """Accept .step, .stp, and other STEP-family extensions."""
    p = Path(path)
    if p.exists():
        return p
    # Try the alternate extension
    for ext in (".step", ".stp", ".STEP", ".STP"):
        alt = p.with_suffix(ext)
        if alt.exists():
            return alt
    raise FileNotFoundError(f"STEP file not found: {path}")


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
    # step_path = Path(step_path)
    step_path = _resolve_step_path(step_path)   # <-- CHANGE

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
    # boundary_path = Path(boundary_path)
    boundary_path = _resolve_step_path(boundary_path)   # <-- CHANGE

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


def resolve_inputs(folder: str | Path) -> tuple[Path, Path]:
    """Given a folder, find the surface and boundary STEP files.

    A file with faces is the surface; a file with only edges is the
    boundary. Both .step and .stp extensions are recognized.

    Parameters
    ----------
    folder : str | Path
        Folder containing two STEP files.

    Returns
    -------
    (surface_path, boundary_path)

    Raises
    ------
    FileNotFoundError
        If the folder doesn't contain exactly one surface and one boundary.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    # Collect all STEP candidates
    candidates: list[Path] = []
    for ext in ("*.step", "*.stp", "*.STEP", "*.STP"):
        candidates.extend(folder.glob(ext))

    if len(candidates) < 2:
        raise FileNotFoundError(
            f"Expected at least 2 STEP files in {folder}, found {len(candidates)}"
        )

    surfaces: list[Path] = []
    boundaries: list[Path] = []
    other: list[Path] = []

    for path in candidates:
        kind = _probe_step_file(path)
        if kind == "surface":
            surfaces.append(path)
        elif kind == "boundary":
            boundaries.append(path)
        else:
            other.append(path)

    if len(surfaces) == 0:
        raise FileNotFoundError(
            f"No surface file found in {folder}. Candidates: {[p.name for p in candidates]}"
        )
    if len(boundaries) == 0:
        raise FileNotFoundError(
            f"No boundary file found in {folder}. Candidates: {[p.name for p in candidates]}"
        )
    if len(surfaces) > 1:
        raise FileNotFoundError(
            f"Ambiguous: multiple surface files found in {folder}: "
            f"{[p.name for p in surfaces]}"
        )
    if len(boundaries) > 1:
        raise FileNotFoundError(
            f"Ambiguous: multiple boundary files found in {folder}: "
            f"{[p.name for p in boundaries]}"
        )

    return surfaces[0], boundaries[0]


def _probe_step_file(path: Path) -> str:
    """Load a STEP file in a temp doc and classify it.

    Returns 'surface', 'boundary', or 'unknown'.
    """
    doc = None
    try:
        doc = FreeCAD.newDocument("_Probe")
        Part.insert(str(path), doc.Name)
        doc.recompute()

        shapes = [obj.Shape for obj in doc.Objects if hasattr(obj, "Shape")]
        if not shapes:
            return "unknown"

        # Combine all shapes' faces and edges
        total_faces = sum(len(s.Faces) for s in shapes)
        total_edges = sum(len(s.Edges) for s in shapes)

        if total_faces > 0:
            return "surface"
        if total_edges > 0:
            return "boundary"
        return "unknown"
    except Exception:
        return "unknown"
    finally:
        if doc is not None:
            try:
                FreeCAD.closeDocument(doc.Name)
            except Exception:
                pass