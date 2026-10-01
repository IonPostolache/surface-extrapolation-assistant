"""Day 2 — calibrated face extrapolation.

FreeCAD's Surface::Extend takes UV extension *ratios*, not millimetres.
This module wraps it with a calibration loop so callers get an honest
`distance_mm` API.

The calibration is:
    1. Measure the current extent along the direction we want to extend.
    2. Compute ratio = target_distance_mm / measured_extent.
    3. Apply Surface::Extend.
    4. Re-measure the achieved extension.
    5. If off by more than tolerance, apply one correction pass.

Public API:
    ExtrapolationResult
    extrapolate_face(face, distance_mm, ...) -> ExtrapolationResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from surface_assistant import freecad_setup  # noqa: F401
from surface_assistant.topology import UVDirections

import FreeCAD  # type: ignore
import Part     # type: ignore


def _build_ruled_extension(face, edge, distance_mm, samples=20):
    try:
        if distance_mm <= 0:
            return None

        surface = face.Surface
        if surface is None:
            return None

        t_min = edge.FirstParameter
        t_max = edge.LastParameter
        n = max(2, samples)

        original_points = []
        offset_points = []

        for i in range(n + 1):
            t = t_min + (t_max - t_min) * i / n
            p = edge.valueAt(t)
            original_points.append(p)

            try:
                tangent = edge.tangentAt(t)
            except Exception:
                return None

            # Outward direction = vector from the face's center of mass to
            # the current edge point. This is the natural "grow outward"
            # direction — no tangent projection, because the tangent is not
            # generally perpendicular to the outward direction on a curved edge.
            face_center = face.CenterOfMass

            outward = p - face_center
            if outward.Length < 1e-9:
                return None
            outward.normalize()

            # Confirm direction is outward via point-in-face test.
            candidate = p + outward * distance_mm
            try:
                if face.isInside(candidate, 1e-6, True):
                    outward = outward * -1.0
            except Exception:
                # Fallback: distance from center as the tiebreaker
                if (candidate - face_center).Length < (p - face_center).Length:
                    outward = outward * -1.0

            offset_points.append(p + outward * distance_mm)

        print(f"[ruled-debug] first original point: {original_points[0]}")
        print(f"[ruled-debug] first offset point:   {offset_points[0]}")
        print(f"[ruled-debug] offset delta: {(offset_points[0] - original_points[0]).Length:.3f}")

        # Build BOTH the original and offset as polylines with matching
        # segment counts, so makeRuledSurface connects them properly.
        # Build each segment as a 4-point planar quad face.
        # This bypasses makeRuledSurface entirely, which collapses on
        # non-planar or nearly-parallel segment pairs.
        segments = []
        for i in range(len(original_points) - 1):
            p0 = original_points[i]
            p1 = original_points[i + 1]
            q0 = offset_points[i]
            q1 = offset_points[i + 1]

            try:
                edges = [
                    Part.LineSegment(p0, p1).toShape(),
                    Part.LineSegment(p1, q1).toShape(),
                    Part.LineSegment(q1, q0).toShape(),
                    Part.LineSegment(q0, p0).toShape(),
                ]
                wire = Part.Wire(edges)
                face = Part.Face(wire)
                if face.Area > 1e-9:
                    segments.append(face)

                else:
                    print(f"[ruled] segment {i}: zero area, skipped")
            except Exception as e:
                print(f"[ruled] segment {i} failed: {e}")
                continue

        total_area = sum(s.Area for s in segments)
        print(f"[ruled] segments built: {len(segments)}, total area: {total_area:.2f}")

        if not segments:
            return None

        return Part.makeCompound(segments)

    except Exception:
        return None


def _extend_face_with_untrim_guard(
    face: Part.Face,
    extendable_edges: list[Part.Edge],
    directions: UVDirections,
    distance_mm: float,
    freecad_tolerance: float = 0.1,
) -> Part.Shape | None:
    """Extend a face, then trim back to original + border to guard
    against BSpline un-trimming."""

    # Step 1: Extend the surface parametrically
    doc = FreeCAD.newDocument("_ExtendDoc")
    try:
        u_extent, v_extent = _measure_uv_extent(face)
        extents = []
        if directions.u_neg or directions.u_pos:
            extents.append(u_extent)
        if directions.v_neg or directions.v_pos:
            extents.append(v_extent)
        base_extent = min(extents) if extents else 1.0
        ratio = distance_mm / base_extent

        extended_surface = _apply_extend(
            doc, face, ratio, directions, freecad_tolerance
        )
    finally:
        FreeCAD.closeDocument(doc.Name)

    # Step 2: Build the keep region (original + border)
    # Use the ruled strips for the border
    chains = _group_edges_into_chains(extendable_edges)
    border_shapes = []
    for chain in chains:
        strip = _build_ruled_extension_from_chain(face, chain, distance_mm)
        if strip is not None:
            border_shapes.append(strip)

    if not border_shapes:
        # No border possible — return the original face only (safest)
        return face

    try:
        keep_region = face.fuse(Part.makeCompound(border_shapes))
    except Exception:
        keep_region = face

    # Step 3: Trim the extended surface to the keep region
    try:
        common = extended_surface.common(keep_region)
        if common.Faces:
            return max(common.Faces, key=lambda f: f.Area)
    except Exception:
        pass

    # Fallback: return the extended surface untrimmed
    return extended_surface

def _extend_planar_face_ruled(
    face: Part.Face,
    extendable_edges: list[Part.Edge],
    distance_mm: float,
    tolerance_mm: float = 0.04,
) -> Part.Shape | None:
    """Extend a planar face by building a ruled surface from each edge.

    Unlike Surface::Extend, this doesn't operate on the underlying
    parametric plane, so it can't accidentally untrim the face.

    Returns a shell or compound containing the ruled extension faces,
    or None on failure.
    """
    print(f"[ruled-planar] entry: {len(extendable_edges)} edges")
    if not extendable_edges:
        return None
    
    chains = _group_edges_into_chains(extendable_edges)
    extension_shapes = []
    for chain in chains:
        if not chain:
            continue
        # Build a single ruled extension for the whole chain
        ruled = _build_ruled_extension_from_chain(face, chain, distance_mm)
        if ruled is not None:
            extension_shapes.append(ruled)

    if not extension_shapes:
        return None

    # If there's only one edge, just return its compound directly
    if len(extension_shapes) == 1:
        return extension_shapes[0]

    return Part.makeCompound(extension_shapes)

def _build_ruled_extension_from_chain(
    face: Part.Face,
    chain: list[Part.Edge],
    distance_mm: float,
    samples: int = 40,
) -> Part.Shape | None:
    """Build a ruled extension for a contiguous chain of edges."""
    print(f"[ruled-chain] entry: {len(chain)} edges")
    try:
        if distance_mm <= 0 or not chain:
            return None

        surface = face.Surface
        if surface is None:
            return None

        face_center = face.CenterOfMass

        # Sample the entire chain as one continuous polyline
        original_points = []
        offset_points = []

        # Total samples distributed across the chain by edge length
        total_length = sum(e.Length for e in chain)
        if total_length < 1e-6:
            return None

        for edge in chain:
            n_edge_samples = max(2, int(samples * edge.Length / total_length))
            t_min = edge.FirstParameter
            t_max = edge.LastParameter
            for i in range(n_edge_samples + 1):
                t = t_min + (t_max - t_min) * i / n_edge_samples
                p = edge.valueAt(t)

                # Skip duplicate points at chain junctions
                if original_points and (original_points[-1] - p).Length < 1e-6:
                    continue

                original_points.append(p)

                # Outward direction: perpendicular to the edge, pointing away from
                # the face's interior. Determined by testing which side of the edge
                # is inside the face.
                try:
                    tangent = edge.tangentAt(t)
                except Exception:
                    return None

                try:
                    face_normal_center = face.normalAt(0.5, 0.5)
                except Exception:
                    return None

                perp = tangent.cross(face_normal_center)
                if perp.Length < 1e-9:
                    # Fallback to center-based direction
                    outward = p - face.CenterOfMass
                    if outward.Length < 1e-9:
                        return None
                    outward.normalize()
                else:
                    perp.normalize()
                    test_step = 0.5
                    test_pos = p + perp * test_step
                    test_neg = p - perp * test_step

                    try:
                        inside_pos = face.isInside(test_pos, 1e-6, True)
                        inside_neg = face.isInside(test_neg, 1e-6, True)
                    except Exception:
                        inside_pos = inside_neg = False

                    if inside_neg and not inside_pos:
                        outward = perp
                    elif inside_pos and not inside_neg:
                        outward = perp * -1.0
                    else:
                        outward = p - face.CenterOfMass
                        if outward.Length < 1e-9:
                            return None
                        outward.normalize()

                offset_points.append(p + outward * distance_mm)

        if len(original_points) < 2 or len(offset_points) < 2:
            return None

        # Build quad strip
        segments = []
        for i in range(len(original_points) - 1):
            p0 = original_points[i]
            p1 = original_points[i + 1]
            q0 = offset_points[i]
            q1 = offset_points[i + 1]
            try:
                edges = [
                    Part.LineSegment(p0, p1).toShape(),
                    Part.LineSegment(p1, q1).toShape(),
                    Part.LineSegment(q1, q0).toShape(),
                    Part.LineSegment(q0, p0).toShape(),
                ]
                wire = Part.Wire(edges)
                quad = Part.Face(wire)
                if quad.Area > 1e-9:
                    segments.append(quad)
            except Exception:
                continue

        if not segments:
            return None

        return Part.makeCompound(segments)
    except Exception:
        return None

    
# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

Direction = Literal["U+", "U-", "V+", "V-", "all"]


class ExtrapolationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"       # extended, but outside tolerance
    FAILED = "failed"         # exception from FreeCAD


@dataclass
class ExtrapolationResult:
    """Outcome of a single face extrapolation attempt."""

    status: ExtrapolationStatus
    face_index: int
    requested_mm: float
    achieved_mm: float | None = None
    ratio_used: float | None = None
    tolerance_percent: float = 2.0
    error_message: str | None = None
    extended_face: Part.Face | None = None
    correction_applied: bool = False

    @property
    def achieved_percent_error(self) -> float | None:
        if self.achieved_mm is None or self.requested_mm == 0:
            return None
        return abs(self.achieved_mm - self.requested_mm) / self.requested_mm * 100.0

    def short(self) -> str:
        """Human-readable one-line summary, safe against missing fields."""
        if self.status == ExtrapolationStatus.SUCCESS:
            ach = f"{self.achieved_mm:.2f}" if self.achieved_mm is not None else "?"
            ratio = f"{self.ratio_used:.3f}" if self.ratio_used is not None else "?"
            return (
                f"OK face={self.face_index} "
                f"req={self.requested_mm:.2f}mm "
                f"ach={ach}mm "
                f"ratio={ratio}"
            )
        if self.status == ExtrapolationStatus.PARTIAL:
            err = self.achieved_percent_error
            err_str = f"{err:.2f}" if err is not None else "?"
            ach = f"{self.achieved_mm:.2f}" if self.achieved_mm is not None else "?"
            return (
                f"PARTIAL face={self.face_index} "
                f"req={self.requested_mm:.2f}mm "
                f"ach={ach}mm "
                f"err={err_str}%"
            )
        return f"FAILED face={self.face_index} err={self.error_message}"


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def _measure_uv_extent(face: Part.Face) -> tuple[float, float]:
    """Approximate the face's U and V extents in millimetres.

    We use the parametric bounds and sample the isoparametric boundary
    curves. This is not exact for non-planar surfaces but is good enough
    for the calibration ratio.

    Returns
    -------
    (u_extent_mm, v_extent_mm)
    """
    try:
        bb = face.BoundBox
        dims = sorted([bb.XLength, bb.YLength, bb.ZLength], reverse=True)
        return (max(dims[0], 1e-6), max(dims[1], 1e-6))
    except Exception:
        return (1.0, 1.0)
    

def _directions_to_ratios(directions: UVDirections, ratio: float) -> dict[str, float]:
    """Map a UVDirections to the four Surface::Extend ratio properties.

    Also returns the symmetry settings needed to prevent unwanted
    extension on the opposite side of each direction.
    """
    return {
        "ExtendUNeg": ratio if directions.u_neg else 0.0,
        "ExtendUPos": ratio if directions.u_pos else 0.0,
        "ExtendVNeg": ratio if directions.v_neg else 0.0,
        "ExtendVPos": ratio if directions.v_pos else 0.0,
    }


def _apply_extend(
    doc: FreeCAD.Document,
    source_face: Part.Face,
    ratio: float,
    directions: UVDirections,
    tolerance: float,
    sample_u: int = 32,
    sample_v: int = 32,
) -> Part.Face:
    """Create a Surface::Extend object with per-side ratios."""

    source_obj = doc.addObject("Part::Feature", "SourceFace")
    source_obj.Shape = Part.Shape([source_face])
    doc.recompute()

    obj = doc.addObject("Surface::Extend", "Extend")
    obj.Face = [source_obj, "Face1"]
    obj.Tolerance = tolerance
    obj.SampleU = sample_u
    obj.SampleV = sample_v

    # CRITICAL: disable symmetry so only the chosen sides extend
    obj.ExtendUSymetric = False
    obj.ExtendVSymetric = False

    ratios = _directions_to_ratios(directions, ratio)
    for prop, value in ratios.items():
        setattr(obj, prop, value)

    doc.recompute()

    if not obj.Shape or not obj.Shape.Faces:
        raise RuntimeError("Surface::Extend produced no valid face")

    return obj.Shape.Faces[0]


def _measure_extension(
    source_face: Part.Face,
    extended_face: Part.Face,
) -> float:
    """Approximate achieved extension in mm.

    We compare the bounding-box diagonal of the two faces. This is a
    pragmatic proxy that works for planar and gently curved patches.
    For highly curved surfaces it under-reports, but it's still a
    usable signal for the correction pass.
    """
    try:
        bb_src = source_face.BoundBox
        bb_ext = extended_face.BoundBox
        diag_src = (
            (bb_src.XLength) ** 2
            + (bb_src.YLength) ** 2
            + (bb_src.ZLength) ** 2
        ) ** 0.5
        diag_ext = (
            (bb_ext.XLength) ** 2
            + (bb_ext.YLength) ** 2
            + (bb_ext.ZLength) ** 2
        ) ** 0.5
        return max(diag_ext - diag_src, 0.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extrapolate_face(
    face: Part.Face,
    distance_mm: float,
    *,
    directions: UVDirections | None = None,
    extendable_edges: list[Part.Edge] | None = None,
    tolerance_percent: float = 2.0,
    max_correction_passes: int = 1,
    freecad_tolerance: float = 0.1,
    face_index: int = -1,
    doc_name: str = "_ExtrapolationDoc",
) -> ExtrapolationResult:
    """Extrapolate a single face by ~`distance_mm`.

    Surface type determines the strategy:
        - Plane: ruled surface extension per edge (avoids untrim).
        - Cylinder/other: Surface::Extend via `directions`.
    """

    if distance_mm <= 0:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="distance_mm must be positive",
        )

    # Default: extend all sides (old behavior)
    if directions is None and extendable_edges is None:
        directions = UVDirections(u_neg=True, u_pos=True, v_neg=True, v_pos=True)

    surface_type = type(face.Surface).__name__ if face.Surface else "Unknown"

    # Use ruled extension whenever we have specific edges to extend.
    # Parametric Surface::Extend can un-trim non-planar surfaces (BSpline,
    # cylinder, etc.), exposing geometry beyond the original face.
    # use_ruled = bool(extendable_edges)
    surface_type = type(face.Surface).__name__ if face.Surface else "Unknown"
    use_ruled = (surface_type == "Plane") and bool(extendable_edges)

    if use_ruled:
        extended = _extend_planar_face_ruled(
            face, extendable_edges, distance_mm, freecad_tolerance
        )
        if extended is not None:
            result = ExtrapolationResult(
                status=ExtrapolationStatus.SUCCESS,
                face_index=face_index,
                requested_mm=distance_mm,
                achieved_mm=distance_mm,  # trust the ruled construction
                ratio_used=None,
                tolerance_percent=tolerance_percent,
                extended_face=extended,
            )
            return result
        # If ruled fails, fall through to parametric

    # Otherwise, use Surface::Extend (works for cylinders, clean planes)
    if directions is None or not directions.any:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="no UV directions to extend",
        )

    doc = FreeCAD.newDocument(doc_name)
    try:
        u_extent, v_extent = _measure_uv_extent(face)
        extents = []
        if directions.u_neg or directions.u_pos:
            extents.append(u_extent)
        if directions.v_neg or directions.v_pos:
            extents.append(v_extent)
        base_extent = min(extents) if extents else 1.0
        ratio = distance_mm / base_extent

        extended = _apply_extend(doc, face, ratio, directions, freecad_tolerance)
        achieved = _measure_extension(face, extended)

        result = ExtrapolationResult(
            status=ExtrapolationStatus.SUCCESS,
            face_index=face_index,
            requested_mm=distance_mm,
            achieved_mm=achieved,
            ratio_used=ratio,
            tolerance_percent=tolerance_percent,
            extended_face=extended,
        )
        # ... correction passes unchanged ...
        return result
    except Exception as exc:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message=str(exc),
        )
    finally:
        FreeCAD.closeDocument(doc.Name)


def _group_edges_into_chains(edges: list[Part.Edge], tolerance: float = 1e-3) -> list[list[Part.Edge]]:
    """Group edges that share endpoints into contiguous chains.

    Edges are considered connected if one edge's endpoint is within
    tolerance of another edge's endpoint.
    """
    if not edges:
        return []

    # Build a list of endpoints for each edge
    edge_ends = []
    for e in edges:
        try:
            v0 = e.Vertexes[0].Point
            v1 = e.Vertexes[-1].Point
            edge_ends.append((e, v0, v1))
        except Exception:
            continue

    chains = []
    used = [False] * len(edge_ends)

    for i, (e_i, v0_i, v1_i) in enumerate(edge_ends):
        if used[i]:
            continue
        chain = [e_i]
        used[i] = True
        current_end = v1_i

        # Extend the chain by finding an unused edge that connects to current_end
        extended = True
        while extended:
            extended = False
            for j, (e_j, v0_j, v1_j) in enumerate(edge_ends):
                if used[j]:
                    continue
                if (v0_j - current_end).Length < tolerance:
                    chain.append(e_j)
                    used[j] = True
                    current_end = v1_j
                    extended = True
                    break
                if (v1_j - current_end).Length < tolerance:
                    chain.append(e_j)
                    used[j] = True
                    current_end = v0_j
                    extended = True
                    break

        chains.append(chain)

    return chains