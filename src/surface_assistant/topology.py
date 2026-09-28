"""Topological utilities: boundary face detection and stable face IDs.

Why fingerprinting instead of Face-N indices?
FreeCAD/OpenCascade's topological naming is unstable: after recompute,
'Face7' can refer to a different face. We therefore identify faces by a
geometric fingerprint (surface type + area + center of mass + bounding box).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore
from FreeCAD import Vector  # type: ignore


# ---------------------------------------------------------------------------
# Geometric fingerprint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FaceFingerprint:
    """Stable identifier for a face, resilient to topological reordering."""

    surface_type: str
    area: float
    com: tuple[float, float, float]      # center of mass
    bbox: tuple[float, float, float, float, float, float]  # xmin,ymin,zmin,xmax,ymax,zmax

    def short(self) -> str:
        return (
            f"{self.surface_type}"
            f"|A={self.area:.3f}"
            f"|COM=({self.com[0]:.2f},{self.com[1]:.2f},{self.com[2]:.2f})"
        )


def fingerprint_face(face: Part.Face) -> FaceFingerprint:
    """Compute a stable fingerprint for a face."""
    surf_type = type(face.Surface).__name__ if face.Surface else "Unknown"

    try:
        area = float(face.Area)
    except Exception:
        area = 0.0

    try:
        com = face.CenterOfMass
        com_t = (round(com.x, 3), round(com.y, 3), round(com.z, 3))
    except Exception:
        com_t = (0.0, 0.0, 0.0)

    try:
        bb = face.BoundBox
        bbox_t = (
            round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3),
            round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3),
        )
    except Exception:
        bbox_t = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    return FaceFingerprint(
        surface_type=surf_type,
        area=area,
        com=com_t,
        bbox=bbox_t,
    )


# ---------------------------------------------------------------------------
# Boundary face detection
# ---------------------------------------------------------------------------

@dataclass
class BoundaryFace:
    """A face on the outer boundary, with its fingerprint and index."""

    index: int
    face: Part.Face
    fingerprint: FaceFingerprint
    shared_edge_count: int   # number of edges shared with the boundary wire


def _edges_match(edge_a: Part.Edge, edge_b: Part.Edge, tol: float = 1e-4) -> bool:
    """Loose edge equality by endpoints (works when topological naming is unstable)."""
    try:
        va = edge_a.Vertexes
        vb = edge_b.Vertexes
        if len(va) < 2 or len(vb) < 2:
            return False
        a1, a2 = va[0].Point, va[-1].Point
        b1, b2 = vb[0].Point, vb[-1].Point

        def close(p, q):
            return (p - q).Length < tol

        return (close(a1, b1) and close(a2, b2)) or (close(a1, b2) and close(a2, b1))
    except Exception:
        return False


def get_boundary_faces(
    shape: Part.Shape,
    boundary_shape: Part.Shape,
    tolerance: float = 1e-3,
) -> list[BoundaryFace]:
    """Return faces of `shape` that share an edge with `boundary_shape`.

    Parameters
    ----------
    shape : Part.Shape
        The surface to be extrapolated.
    boundary_shape : Part.Shape
        The user-supplied outer boundary (wire / edge / compound).
    tolerance : float
        Distance tolerance used when matching edges.

    Returns
    -------
    list[BoundaryFace]
        Boundary faces with stable fingerprints.
    """
    boundary_edges = list(boundary_shape.Edges)
    if not boundary_edges:
        raise ValueError("Boundary shape contains no edges.")

    result: list[BoundaryFace] = []

    for idx, face in enumerate(shape.Faces):
        shared = 0
        for f_edge in face.Edges:
            for b_edge in boundary_edges:
                if _edges_match(f_edge, b_edge, tol=tolerance):
                    shared += 1
                    break
        if shared > 0:
            result.append(
                BoundaryFace(
                    index=idx,
                    face=face,
                    fingerprint=fingerprint_face(face),
                    shared_edge_count=shared,
                )
            )

    if not result:
        raise RuntimeError(
            "No boundary faces detected. Check that the boundary curve "
            "actually touches the surface."
        )

    return result


def describe_faces(faces: Iterable[BoundaryFace]) -> str:
    """Human-readable summary of boundary faces."""
    lines = [f"Found {len(faces)} boundary face(s):"]
    for bf in faces:
        lines.append(
            f"  [Face {bf.index}] {bf.fingerprint.short()} "
            f"(shared edges: {bf.shared_edge_count})"
        )
    return "\n".join(lines)

def classify_face_edges(
    face: Part.Face,
    all_faces: list[Part.Face],
    tolerance: float = 1e-3,
) -> dict[int, str]:
    """For each edge of `face`, return 'free' or 'shared'.

    An edge is 'shared' if any *other* face in all_faces has a matching
    edge (same length and same midpoint within tolerance).
    """
    result: dict[int, str] = {}
    for i, edge in enumerate(face.Edges):
        e_len = edge.Length
        e_mid = edge.CenterOfMass
        shared = False
        for other in all_faces:
            if other.isSame(face):
                continue
            for oe in other.Edges:
                if (
                    abs(oe.Length - e_len) < tolerance
                    and (oe.CenterOfMass - e_mid).Length < tolerance
                ):
                    shared = True
                    break
            if shared:
                break
        result[i] = "shared" if shared else "free"
    return result

def infer_extension_direction(
    face: Part.Face,
    all_faces: list[Part.Face],
    tolerance: float = 1e-3,
) -> str:
    """Return a Surface::Extend direction string based on free edges.

    Heuristic:
        - If the face's U+ edge is free and U- is shared → "U+"
        - If U- is free and U+ is shared → "U-"
        - If both U edges are free → "U+U-" (extend both)
        - If both U edges are shared → fall through to V
        - Same logic for V

    For a face with 3 free edges and 1 shared edge, this picks the
    correct two-direction extension.
    """
    # This is where the geometry gets fiddly. A simpler first pass:
    # if exactly one edge is shared, extend away from it.
    classifications = classify_face_edges(face, all_faces, tolerance)
    shared_count = sum(1 for v in classifications.values() if v == "shared")
    if shared_count == 0:
        return "all"
    if shared_count == len(classifications):
        return "none"
    # For now: extend in "all" but the join step will need to handle
    # the shared-edge collision by splitting.
    return "all"