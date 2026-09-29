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


def _point_on_boundary(
    point: FreeCAD.Vector,
    boundary_edges: list,
    tolerance: float,
) -> bool:
    """Check if a 3D point lies on any boundary edge within tolerance."""
    for edge in boundary_edges:
        try:
            # distToShape returns (distance, points, subshapes, params)
            dist = edge.distToShape(Part.Vertex(point))[0]
            if dist < tolerance:
                return True
        except Exception:
            continue
    return False


def _side_touches_boundary(
    surface,
    fixed_param: float,
    is_u_iso: bool,
    param_min: float,
    param_max: float,
    boundary_edges: list,
    tolerance: float,
    samples: int = 5,
) -> bool:
    """Sample an isoparametric curve and check if any sample is on the boundary.

    is_u_iso=True means this is a uIso curve (fixed u, varying v).
    is_u_iso=False means this is a vIso curve (fixed v, varying u).
    """
    try:
        if is_u_iso:
            curve = surface.uIso(fixed_param)
            params = [
                param_min + (param_max - param_min) * i / (samples - 1)
                for i in range(samples)
            ]
        else:
            curve = surface.vIso(fixed_param)
            params = [
                param_min + (param_max - param_min) * i / (samples - 1)
                for i in range(samples)
            ]

        for p in params:
            try:
                pt = curve.value(p)
            except Exception:
                continue
            if _point_on_boundary(pt, boundary_edges, tolerance):
                return True
        return False
    except Exception:
        return False


def infer_uv_directions(
    face: Part.Face,
    all_faces: list[Part.Face],
    tolerance: float = 1e-3,
) -> UVDirections:
    """Infer which UV sides of `face` are free (not shared with a neighbor).

    An edge is free if no other face in `all_faces` shares it. Free edges
    define where the face should be extended.

    Maps each free edge back to its UV side using the face's parameterization.
    """
    result = UVDirections()

    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
    except Exception:
        return result

    surface = face.Surface
    classifications = classify_face_edges(face, all_faces, tolerance)

    # For each edge, determine which UV side it lies on by sampling
    # points and comparing to the parameter-space boundaries.
    for edge, cls in zip(face.Edges, classifications):
        if cls != "free":
            continue

        # Sample the edge's midpoint and find its (u, v) on the surface
        mid = edge.CenterOfMass
        try:
            u, v = surface.parameter(mid)
        except Exception:
            continue

        # Which boundary is closest?
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
        elif d == dv_max:
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