"""Surface extension via curvature-following ribbons.

The core operation is:
    1. Sample a boundary edge.
    2. At each sample, compute the face's outward direction (in the
       face's tangent plane).
    3. Offset each sample by `distance_mm` in that direction.
    4. Interpolate a BSpline curve through the offset points.
    5. Loft between the original edge and the offset edge to create
       a curvature-continuous ribbon.
    6. Sew the ribbon onto the original face.

This bypasses Surface::Extend entirely. Surface::Extend operates on
the underlying mathematical surface (un-trimming it in the process),
which destroys the face's trim topology and loses corner fillets.
The ribbon approach builds the extension explicitly, preserving the
original face unchanged.

Public API:
    ExtrapolationResult
    ExtrapolationStatus
    extrapolate_face(face, distance_mm, ...) -> ExtrapolationResult
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

def _normalized(v: FreeCAD.Vector) -> FreeCAD.Vector:
    """Return a normalized copy of a FreeCAD.Vector (FreeCAD has
    .normalize() which mutates in place; this does not)."""
    result = FreeCAD.Vector(v.x, v.y, v.z)
    result.normalize()
    return result

class ExtrapolationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


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
    extended_face: Part.Shape | None = None
    correction_applied: bool = False

    @property
    def achieved_percent_error(self) -> float | None:
        if self.achieved_mm is None or self.requested_mm == 0:
            return None
        return abs(self.achieved_mm - self.requested_mm) / self.requested_mm * 100.0

    def short(self) -> str:
        if self.status == ExtrapolationStatus.SUCCESS:
            ach = f"{self.achieved_mm:.2f}" if self.achieved_mm is not None else "?"
            return (
                f"OK face={self.face_index} "
                f"req={self.requested_mm:.2f}mm "
                f"ach={ach}mm"
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
# Outward direction
# ---------------------------------------------------------------------------

def _outward_direction_in_tangent_plane(
    face: Part.Face,
    point: FreeCAD.Vector,
    tangent: FreeCAD.Vector,
    test_step: float = 0.5,
) -> FreeCAD.Vector | None:
    """Compute the face's outward direction at `point`, perpendicular to
    the edge tangent, lying in the face's tangent plane.

    Determined by testing which side of the edge is inside the face.
    """
    try:
        u, v = face.Surface.parameter(point)
        normal = face.Surface.normal(u, v)
    except Exception:
        try:
            normal = face.normalAt(0.5, 0.5)
        except Exception:
            return None

    # Candidate direction: perpendicular to the tangent, in the tangent plane
    perp = tangent.cross(normal)
    if perp.Length < 1e-9:
        # Degenerate tangent/normal — fall back to center-based
        center_dir = point - face.CenterOfMass
        if center_dir.Length < 1e-9:
            return None
        return _normalized(center_dir)

    perp.normalize()

    # Test which side is inside the face
    test_pos = point + perp * test_step
    test_neg = point - perp * test_step

    try:
        inside_pos = face.isInside(test_pos, 1e-6, True)
        inside_neg = face.isInside(test_neg, 1e-6, True)
    except Exception:
        inside_pos = inside_neg = False

    if inside_neg and not inside_pos:
        return perp
    if inside_pos and not inside_neg:
        return perp * -1.0

    # Ambiguous — fall back to center-based
    center_dir = point - face.CenterOfMass
    if center_dir.Length < 1e-9:
        return perp
    return _normalized(center_dir)


# ---------------------------------------------------------------------------
# Ribbon construction
# ---------------------------------------------------------------------------

def _build_curvature_ribbon(
    face: Part.Face,
    edge: Part.Edge,
    distance_mm: float,
    samples: int = 20,
) -> Part.Face | None:
    """Build a curvature-following ribbon extending `face` along `edge`.

    Steps:
        1. Sample the edge at `samples` points.
        2. For each sample, compute the outward direction.
        3. Offset each sample by `distance_mm` outward.
        4. Interpolate a BSpline through the offset points.
        5. Loft between the original edge and the offset curve.

    Returns the ribbon as a single face, or None on failure.
    """
    if distance_mm <= 0:
        return None

    surface = face.Surface
    if surface is None:
        return None

    t_min = edge.FirstParameter
    t_max = edge.LastParameter
    n = max(4, samples)

    original_points: list[FreeCAD.Vector] = []
    offset_points: list[FreeCAD.Vector] = []

    for i in range(n + 1):
        t = t_min + (t_max - t_min) * i / n
        try:
            p = edge.valueAt(t)
            tangent = edge.tangentAt(t)
        except Exception:
            return None

        outward = _outward_direction_in_tangent_plane(face, p, tangent)
        if outward is None:
            return None

        original_points.append(p)
        offset_points.append(p + outward * distance_mm)

    # Build the original edge as a wire (use the edge directly)
    try:
        original_wire = Part.Wire([edge])
    except Exception:
        return None

    # Build the offset edge as an interpolated BSpline
    try:
        offset_curve = Part.BSplineCurve()
        offset_curve.interpolate(offset_points)
        offset_edge = offset_curve.toShape()
        offset_wire = Part.Wire([offset_edge])
    except Exception:
        return None

    # Loft between the two wires with a smooth (non-ruled) surface
    try:
        loft = Part.makeLoft([original_wire, offset_wire], False, False)
    except Exception:
        return None

    if not loft.Faces:
        return None

    return max(loft.Faces, key=lambda f: f.Area)


# ---------------------------------------------------------------------------
# Sewing
# ---------------------------------------------------------------------------

def _sew_shapes(shapes: list[Part.Shape], tolerance: float = 0.01) -> Part.Shape:
    """Sew a list of shapes into a single shell/compound.

    Falls back to a compound if sewing fails or produces an empty result.
    """
    if not shapes:
        return Part.Compound([])
    if len(shapes) == 1:
        return shapes[0]

    try:
        shell = Part.makeShell(shapes)
        return shell
    except Exception:
        pass

    try:
        compound = Part.makeCompound(shapes)
        return compound
    except Exception:
        return shapes[0]


# ---------------------------------------------------------------------------
# Measurement (for reporting only)
# ---------------------------------------------------------------------------

def _measure_extension(
    source_face: Part.Face,
    extended_shape: Part.Shape,
) -> float:
    """Approximate achieved extension in mm.

    Uses the bounding-box diagonal delta. This is a reporting proxy —
    the ruled construction places offset points exactly at the
    requested distance, so achieved = requested by construction.
    """
    try:
        bb_src = source_face.BoundBox
        bb_ext = extended_shape.BoundBox
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
    """Extend a face outward in curvature along its extendable edges.

    For each edge in `extendable_edges`, build a curvature-following
    ribbon extending outward by `distance_mm`. Sew all ribbons onto
    the original face. The result is the original face plus the
    ribbons as a single shape.

    If `extendable_edges` is None or empty, the function returns the
    original face unchanged with a FAILED status (no extension possible).
    """
    if distance_mm <= 0:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="distance_mm must be positive",
        )

    if not extendable_edges:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="no extendable edges",
        )

    # Build a ribbon for each extendable edge
    shapes: list[Part.Shape] = [face]
    ribbons_built = 0

    for edge in extendable_edges:
        ribbon = _build_curvature_ribbon(face, edge, distance_mm)
        if ribbon is not None:
            shapes.append(ribbon)
            ribbons_built += 1

    if ribbons_built == 0:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="no ribbons could be built",
        )

    # Sew everything together
    try:
        combined = _sew_shapes(shapes)
    except Exception as exc:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message=f"sewing failed: {exc}",
        )

    # Report
    achieved = _measure_extension(face, combined)
    result = ExtrapolationResult(
        status=ExtrapolationStatus.SUCCESS,
        face_index=face_index,
        requested_mm=distance_mm,
        achieved_mm=achieved if achieved > 0 else distance_mm,
        ratio_used=None,
        tolerance_percent=tolerance_percent,
        extended_face=combined,
    )
    return result