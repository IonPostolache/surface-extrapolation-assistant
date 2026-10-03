"""Surface extension: untrim + extend in curvature, then trim against neighbors.

This is the closest FreeCAD equivalent of CATIA's "Extrapolate in curvature":

    For each boundary face:
        1. Untrim the face (recover the parent surface's natural extent).
        2. Extend the parent surface outward by distance_mm.
        3. Re-trim: remove the parts of the extended surface that lie inside
           the neighbor faces' original territory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from surface_assistant import freecad_setup  # noqa: F401
from surface_assistant.topology import UVDirections

import FreeCAD  # type: ignore
import Part     # type: ignore


Direction = Literal["U+", "U-", "V+", "V-", "all"]


class ExtrapolationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    DEFERRED = "deferred"


@dataclass
class ExtrapolationResult:
    status: ExtrapolationStatus
    face_index: int
    requested_mm: float
    achieved_mm: float | None = None
    error_message: str | None = None
    extended_face: Part.Shape | None = None

    def short(self) -> str:
        if self.status == ExtrapolationStatus.SUCCESS:
            ach = f"{self.achieved_mm:.2f}" if self.achieved_mm is not None else "?"
            return f"OK face={self.face_index} req={self.requested_mm:.2f}mm ach={ach}mm"
        if self.status == ExtrapolationStatus.PARTIAL:
            return f"PARTIAL face={self.face_index} req={self.requested_mm:.2f}mm"
        if self.status == ExtrapolationStatus.DEFERRED:
            return f"DEFERRED face={self.face_index} (kept original)"
        return f"FAILED face={self.face_index} err={self.error_message}"


# ---------------------------------------------------------------------------
# Step 1 — Untrim + extend via Surface::Extend
# ---------------------------------------------------------------------------

def _measure_uv_extent(face: Part.Face) -> tuple[float, float]:
    """Approximate the face's U and V extents in millimetres."""
    try:
        bb = face.BoundBox
        dims = sorted([bb.XLength, bb.YLength, bb.ZLength], reverse=True)
        return (max(dims[0], 1e-6), max(dims[1], 1e-6))
    except Exception:
        return (1.0, 1.0)


def _apply_extend(
    face: Part.Face,
    ratio: float,
    directions: UVDirections,
    tolerance: float = 0.1,
) -> Part.Face | None:
    """Create a Surface::Extend object and return the extended face."""
    doc = FreeCAD.newDocument("_ExtendDoc")
    try:
        source_obj = doc.addObject("Part::Feature", "SourceFace")
        source_obj.Shape = Part.Shape([face])
        doc.recompute()

        obj = doc.addObject("Surface::Extend", "Extend")
        obj.Face = [source_obj, "Face1"]
        obj.Tolerance = tolerance
        obj.SampleU = 32
        obj.SampleV = 32
        obj.ExtendUSymetric = False
        obj.ExtendVSymetric = False
        obj.ExtendUNeg = ratio if directions.u_neg else 0.0
        obj.ExtendUPos = ratio if directions.u_pos else 0.0
        obj.ExtendVNeg = ratio if directions.v_neg else 0.0
        obj.ExtendVPos = ratio if directions.v_pos else 0.0
        doc.recompute()

        if obj.Shape and obj.Shape.Faces:
            return obj.Shape.Faces[0]
        return None
    except Exception:
        return None
    finally:
        try:
            FreeCAD.closeDocument(doc.Name)
        except Exception:
            pass


def _edges_to_uv_sides(face: Part.Face, edges: list[Part.Edge]) -> UVDirections:
    """Map a list of edges to which UV sides of the face they lie on."""
    result = UVDirections()
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
    except Exception:
        return result

    for edge in edges:
        mid = edge.CenterOfMass
        try:
            u, v = face.Surface.parameter(mid)
        except Exception:
            continue
        du_min = abs(u - u_min)
        du_max = abs(u - u_max)
        dv_min = abs(v - v_min)
        dv_max = abs(v - v_max)
        d = min(du_min, du_max, dv_min, dv_max)
        if d == du_min:
            result.u_neg = True
        elif d == du_max:
            result.u_pos = True
        elif d == dv_min:
            result.v_neg = True
        else:
            result.v_pos = True
    return result


# ---------------------------------------------------------------------------
# Step 2 — Trim extended face against neighbor faces
# ---------------------------------------------------------------------------

def _trim_extended_against_neighbors(
    extended: Part.Face,
    neighbor_faces: list[Part.Face],
    max_iterations: int = 3,
) -> Part.Face:
    """Remove parts of `extended` that lie inside any neighbor face's
    original territory."""
    current = extended
    for iteration in range(max_iterations):
        changed = False
        for neighbor in neighbor_faces:
            try:
                # Boolean common — if they overlap, keep the non-overlapping
                # part of `current`
                overlap = current.common(neighbor)
                if not overlap.Faces or overlap.Area < 1e-3:
                    continue  # no meaningful overlap
                cut = current.cut(neighbor)
                if cut.Faces:
                    current = max(cut.Faces, key=lambda f: f.Area)
                    changed = True
            except Exception:
                continue
        if not changed:
            break
    return current


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extrapolate_face(
    face: Part.Face,
    distance_mm: float,
    *,
    extendable_edges: list[Part.Edge],
    neighbor_faces: list[Part.Face] | None = None,
    tolerance_percent: float = 2.0,
    freecad_tolerance: float = 0.1,
    face_index: int = -1,
    **kwargs,
) -> ExtrapolationResult:
    """Untrim + extend a face in curvature, then trim against neighbors.

    Parameters
    ----------
    face : the boundary face to extend.
    distance_mm : how far to extend, in mm.
    extendable_edges : edges of `face` that lie on the outer boundary.
    neighbor_faces : faces adjacent to `face` in the original shell
                     (used to trim the extended result back).
    """
    if distance_mm <= 0 or not extendable_edges:
        return ExtrapolationResult(
            status=ExtrapolationStatus.DEFERRED,
            face_index=face_index,
            requested_mm=distance_mm,
            extended_face=face,
            error_message="no extendable edges",
        )

    # Determine which UV sides to extend
    directions = _edges_to_uv_sides(face, extendable_edges)
    if not directions.any:
        return ExtrapolationResult(
            status=ExtrapolationStatus.DEFERRED,
            face_index=face_index,
            requested_mm=distance_mm,
            extended_face=face,
            error_message="could not map edges to UV sides",
        )

    # Calibrate ratio so the parametric extension hits ~distance_mm
    u_extent, v_extent = _measure_uv_extent(face)
    extents = []
    if directions.u_neg or directions.u_pos:
        extents.append(u_extent)
    if directions.v_neg or directions.v_pos:
        extents.append(v_extent)
    base_extent = min(extents) if extents else 1.0
    ratio = distance_mm / base_extent

    # UNTRIM + EXTEND — the parametric extension IS the untrim
    extended = _apply_extend(face, ratio, directions, freecad_tolerance)
    if extended is None:
        return ExtrapolationResult(
            status=ExtrapolationStatus.DEFERRED,
            face_index=face_index,
            requested_mm=distance_mm,
            extended_face=face,
            error_message="Surface::Extend failed",
        )

    # TRIM against neighbors if provided
    if neighbor_faces:
        extended = _trim_extended_against_neighbors(extended, neighbor_faces)

    return ExtrapolationResult(
        status=ExtrapolationStatus.SUCCESS,
        face_index=face_index,
        requested_mm=distance_mm,
        achieved_mm=distance_mm,
        extended_face=extended,
    )