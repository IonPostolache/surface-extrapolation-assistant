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

def extrapolate_surface_as_whole(
    outer_boundary_wire: Part.Wire,
    surface_shape: Part.Shape,
    distance_mm: float,
    samples: int = 20,
) -> Part.Shape | None:
    """Extend the entire surface along its outer boundary.

    Builds one ribbon per boundary edge and returns them as a single
    compound. Unlike the earlier version, this does not try to
    interpolate a single closed offset curve — it processes each edge
    independently, so multi-loop boundaries and concave regions are
    handled naturally.

    Returns a compound of ribbons, or None on total failure.
    """
    if distance_mm <= 0:
        return None

    # edge_list = _order_edges_into_chain(list(outer_boundary_wire.Edges))
    edge_list = list(outer_boundary_wire.Edges)
    if not edge_list:
        return None

    ribbons = []
    built = 0

    for edge in edge_list:
        ribbon = _build_ribbon_for_edge(
            surface_shape, edge, distance_mm, samples
        )
        if ribbon is not None:
            ribbons.append(ribbon)
            built += 1

    if not ribbons:
        print("[whole_surface] no ribbons could be built")
        return None

    print(f"[whole_surface] built {built}/{len(edge_list)} ribbons")

    try:
        return Part.makeCompound(ribbons)
    except Exception as exc:
        print(f"[whole_surface] compound failed: {exc}")
        return None

def _build_ribbon_for_edge(
    surface_shape: Part.Shape,
    edge: Part.Edge,
    distance_mm: float,
    samples: int = 20,
) -> Part.Face | None:
    """Build a single curvature-following ribbon along one boundary edge.

    Samples the edge, offsets each sample outward in the surface's
    tangent plane, interpolates a BSpline through the offset points,
    and lofts between the original edge and the offset curve.
    """
    try:
        t_min = edge.FirstParameter
        t_max = edge.LastParameter
    except Exception:
        return None

    if t_max <= t_min:
        return None

    # Use only 3 samples: start, middle, end
    n = 3
    original_pts = []
    offset_pts = []

    for i in range(n + 1):
        t = t_min + (t_max - t_min) * i / n
        try:
            p = edge.valueAt(t)
            tangent = edge.tangentAt(t)
        except Exception:
            continue

        outward = _outward_at_boundary_point(surface_shape, p, tangent)
        if outward is None:
            continue

        original_pts.append(p)
        offset_pts.append(p + outward * distance_mm)

    if len(original_pts) < 2 or len(offset_pts) < 2:
        return None
   
    # Build the original curve as a wire
    try:
        original_wire = Part.Wire([edge])
    except Exception:
        return None

    # Build the offset curve as a BSpline
    try:
        offset_curve = Part.BSplineCurve()
        offset_curve.interpolate(offset_pts)
        offset_edge = offset_curve.toShape()
        offset_wire = Part.Wire([offset_edge])
    except Exception:
        return None

    # Loft between the two wires
    try:
        loft = Part.makeLoft([original_wire, offset_wire], False, False)
    except Exception:
        return None

    if not loft.Faces:
        return None

    # Return the largest face of the loft
    try:
        return max(loft.Faces, key=lambda f: f.Area)
    except Exception:
        return loft.Faces[0]


def _outward_at_boundary_point(shape, point, tangent):
    """Compute outward direction at a boundary point using the surface's
    local tangent frame."""
    best_face = None
    best_normal = None
    best_score = float("inf")

    for face in shape.Faces:
        # Quick reject: point outside the face's bounding box + margin
        try:
            bb = face.BoundBox
            margin = 5.0
            if not (
                bb.XMin - margin <= point.x <= bb.XMax + margin and
                bb.YMin - margin <= point.y <= bb.YMax + margin and
                bb.ZMin - margin <= point.z <= bb.ZMax + margin
            ):
                continue
        except Exception:
            pass

        try:
            u, v = face.Surface.parameter(point)
            surface_pt = face.valueAt(u, v)
            distance = (surface_pt - point).Length

            # Loosen: STEP-imported surfaces rarely reproduce a boundary
            # point exactly. Accept anything within 100 mm — the nearest
            # face will win by the score below.
            if distance > 100.0:
                continue

            normal = face.Surface.normal(u, v)

            # Perpendicularity check — but with the correct API.
            tangent_n = FreeCAD.Vector(tangent.x, tangent.y, tangent.z)
            tangent_n.normalize()
            normal_n = FreeCAD.Vector(normal.x, normal.y, normal.z)
            normal_n.normalize()
            perp_score = abs(tangent_n.dot(normal_n))

            score = distance + perp_score * 10.0
            if score < best_score:
                best_score = score
                best_face = face
                best_normal = normal
        except Exception as exc:
            # Optional: print(f"[whole_surface] face skipped: {exc}")
            continue

    if best_normal is None:
        return None


    # We already found the face this point is on. Use it to decide direction.
    # (The point was on `best_face`'s surface; `best_normal` was its normal.)
    perp = tangent.cross(best_normal)
    if perp.Length < 1e-9:
        return None
    perp.normalize()

    # Sanity: perp should point AWAY from the face's local interior.
    # Use the face's own center as the interior reference.
    try:
        face_center = best_face.CenterOfMass
        if (point - face_center).dot(perp) < 0:
            perp = -perp
    except Exception:
        pass

    return perp


def _point_inside_shape(shape, point, tolerance=1e-3):
    """Return True if `point` is inside any face of `shape` (projected)."""
    for face in shape.Faces:
        try:
            if face.isInside(point, tolerance, True):
                return True
        except Exception:
            continue
    return False


def _smooth_points(points, window=3):
    """Apply a simple moving-average filter to a list of FreeCAD points."""
    if len(points) < window * 2:
        return points
    smoothed = []
    for i in range(len(points)):
        x = y = z = 0.0
        count = 0
        for j in range(max(0, i - window), min(len(points), i + window + 1)):
            x += points[j].x
            y += points[j].y
            z += points[j].z
            count += 1
        smoothed.append(FreeCAD.Vector(x / count, y / count, z / count))
    return smoothed


def _order_edges_into_chain(edges, tolerance=1e-3):
    """Return the edges ordered so each one's start matches the previous
    edge's end, forming a continuous chain. If the input edges form a
    closed loop, the last edge's end matches the first edge's start."""
    if not edges:
        return []

    remaining = list(edges)
    ordered = [remaining.pop(0)]

    while remaining:
        try:
            end = ordered[-1].Vertexes[-1].Point
        except Exception:
            break

        found = False
        for i, e in enumerate(remaining):
            try:
                v0 = e.Vertexes[0].Point
                v1 = e.Vertexes[-1].Point
            except Exception:
                continue

            # Forward match
            if (v0 - end).Length < tolerance:
                ordered.append(e)
                remaining.pop(i)
                found = True
                break

            # Reverse match — flip the edge
            if (v1 - end).Length < tolerance:
                try:
                    if type(e.Curve).__name__ == "Line":
                        e_flipped = Part.LineSegment(v1, v0).toShape()
                    else:
                        e_flipped = Part.Edge(
                            e.Curve, e.LastParameter, e.FirstParameter
                        )
                    ordered.append(e_flipped)
                    remaining.pop(i)
                    found = True
                    break
                except Exception:
                    # Can't flip — add a straight connector and continue
                    ordered.append(e)
                    remaining.pop(i)
                    found = True
                    break

        if not found:
            # Disconnected — return what we have
            break

    return ordered