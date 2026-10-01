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


# ---------------------------------------------------------------------------
# Infer face side
# ---------------------------------------------------------------------------
"""Infer which UV sides of a face touch the free boundary.

For each of the four UV sides (U-, U+, V-, V+), sample the
isoparametric curve at that boundary and check whether the sampled
points lie on any edge of the user-supplied boundary curve.

This tells us which sides to extend: only the sides that are part
of the free outer boundary, not the sides shared with neighbor faces.
"""

@dataclass
class UVDirections:
    """Which UV sides of a face touch the free boundary."""

    u_neg: bool = False
    u_pos: bool = False
    v_neg: bool = False
    v_pos: bool = False

    @property
    def any(self) -> bool:
        return self.u_neg or self.u_pos or self.v_neg or self.v_pos

    def describe(self) -> str:
        parts = []
        if self.u_pos: parts.append("U+")
        if self.u_neg: parts.append("U-")
        if self.v_pos: parts.append("V+")
        if self.v_neg: parts.append("V-")
        return ",".join(parts) if parts else "none"


def _edge_touches_boundary(
    edge: Part.Edge,
    boundary_edges: list,
    tolerance: float,
) -> bool:
    """Check if an edge lies on any boundary edge (same geometry, within tolerance).

    Matches by length and midpoint proximity. This is a looser check than
    endpoint matching because CATIA STEP exports sometimes produce edges
    with the same geometry but slightly different endpoint coordinates.
    """
    e_mid = edge.CenterOfMass
    e_len = edge.Length
    for b_edge in boundary_edges:
        try:
            if abs(b_edge.Length - e_len) < tolerance:
                if (b_edge.CenterOfMass - e_mid).Length < tolerance:
                    return True
        except Exception:
            continue
    return False


def infer_uv_directions(
    face: Part.Face,
    all_faces: list[Part.Face],
    boundary_shape: Part.Shape,
    tolerance: float = 1e-3,
    boundary_tolerance: float = 0.5,
) -> UVDirections:
    """Infer which UV sides of `face` should be extended.

    A side is marked for extension only if:
        1. Its edge is FREE (not shared with a neighbor face), AND
        2. Its edge touches the user-supplied boundary curve.

    Parameters
    ----------
    face : Part.Face
    all_faces : list[Part.Face]
    boundary_shape : Part.Shape
        The user-supplied boundary curve.
    tolerance : float
        Edge-matching tolerance for shared-edge detection.
    boundary_tolerance : float
        Distance tolerance for "edge touches boundary" check.
    """
    result = UVDirections()

    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
    except Exception:
        return result

    surface = face.Surface
    boundary_edges = list(boundary_shape.Edges)

    classifications = classify_face_edges(face, all_faces, tolerance)

    for edge, cls in zip(face.Edges, classifications):
        if cls != "free":
            continue

        # NEW: does this edge touch the boundary curve?
        if not _edge_touches_boundary(edge, boundary_edges, boundary_tolerance):
            continue

        # Sample the edge's midpoint and find its (u, v) on the surface
        mid = edge.CenterOfMass
        try:
            u, v = surface.parameter(mid)
        except Exception:
            continue

        # Determine which UV side this edge lies on
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


def classify_face_edges(
    face: Part.Face,
    all_faces: list[Part.Face],
    tolerance: float = 1e-3,
) -> list[str]:
    """For each edge of `face`, return 'shared' or 'free'.

    An edge is 'shared' if any other face in `all_faces` contains a
    geometrically matching edge.
    """
    classifications = []
    for edge in face.Edges:
        e_mid = edge.CenterOfMass
        e_len = edge.Length
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
        classifications.append("shared" if shared else "free")
    return classifications