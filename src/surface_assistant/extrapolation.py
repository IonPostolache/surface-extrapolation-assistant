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

import FreeCAD  # type: ignore
import Part     # type: ignore


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
    

def _direction_to_ratios(direction: Direction, ratio: float) -> dict[str, float]:
    """Map a direction request to the four Surface::Extend ratio properties."""
    r = {
        "ExtendUNeg": 0.0,
        "ExtendUPos": 0.0,
        "ExtendVNeg": 0.0,
        "ExtendVPos": 0.0,
    }
    if direction == "U+":
        r["ExtendUPos"] = ratio
    elif direction == "U-":
        r["ExtendUNeg"] = ratio
    elif direction == "V+":
        r["ExtendVPos"] = ratio
    elif direction == "V-":
        r["ExtendVNeg"] = ratio
    elif direction == "all":
        r["ExtendUNeg"] = ratio
        r["ExtendUPos"] = ratio
        r["ExtendVNeg"] = ratio
        r["ExtendVPos"] = ratio
    return r


def _apply_extend(
    doc: FreeCAD.Document,
    source_face: Part.Face,
    ratio: float,
    direction: Direction,
    tolerance: float,
    sample_u: int = 32,
    sample_v: int = 32,
) -> Part.Face:
    """Create a Surface::Extend object and return the extended face.

    Surface::Extend requires a LinkSub reference to a face of a
    document object, not a raw Part.Face. So we first add a
    Part::Feature to the document containing our face, then
    reference its "Face1" sub-element.
    """
    # Step 1: Add the source face as a document object so it has
    # addressable sub-elements ("Face1").
    source_obj = doc.addObject("Part::Feature", "SourceFace")
    source_obj.Shape = Part.Shape([source_face])
    doc.recompute()

    # Step 2: Create the Extend object and set the LinkSub.
    obj = doc.addObject("Surface::Extend", "Extend")
    obj.Face = [source_obj, "Face1"]  # LinkSub format
    obj.Tolerance = tolerance
    obj.SampleU = sample_u
    obj.SampleV = sample_v

    ratios = _direction_to_ratios(direction, ratio)
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
    direction: Direction = "all",
    tolerance_percent: float = 2.0,
    max_correction_passes: int = 1,
    freecad_tolerance: float = 0.1,
    face_index: int = -1,
    doc_name: str = "_ExtrapolationDoc",
) -> ExtrapolationResult:
    """Extrapolate a single face by ~`distance_mm` in the given direction.

    Parameters
    ----------
    face : Part.Face
        The face to extend.
    distance_mm : float
        Target extension distance in millimetres.
    direction : Direction
        "U+", "U-", "V+", "V-", or "all".
    tolerance_percent : float
        Acceptable deviation from `distance_mm`, as a percentage.
    max_correction_passes : int
        How many ratio-correction passes to attempt after the first try.
    freecad_tolerance : float
        Geometric tolerance passed to Surface::Extend.
    face_index : int
        For reporting only. Set by the caller.

    Returns
    -------
    ExtrapolationResult
    """
    if distance_mm <= 0:
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message="distance_mm must be positive",
        )

    doc = FreeCAD.newDocument(doc_name)

    try:
        u_extent, v_extent = _measure_uv_extent(face)

        # For "all", use the smaller extent so the ratio is conservative.
        if direction == "all":
            base_extent = min(u_extent, v_extent)
        elif direction in ("U+", "U-"):
            base_extent = u_extent
        else:
            base_extent = v_extent

        ratio = distance_mm / base_extent

        # First attempt
        extended = _apply_extend(
            doc, face, ratio, direction, freecad_tolerance
        )
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

        # Correction passes
        for _ in range(max_correction_passes):
            if achieved <= 0:
                break
            err_pct = abs(achieved - distance_mm) / distance_mm * 100.0
            if err_pct <= tolerance_percent:
                break

            correction_factor = distance_mm / achieved
            ratio *= correction_factor
            extended = _apply_extend(
                doc, face, ratio, direction, freecad_tolerance
            )
            achieved = _measure_extension(face, extended)
            result.ratio_used = ratio
            result.achieved_mm = achieved
            result.extended_face = extended
            result.correction_applied = True

        # Final status
        if result.achieved_percent_error is not None and \
                result.achieved_percent_error > tolerance_percent:
            result.status = ExtrapolationStatus.PARTIAL
        else:
            result.status = ExtrapolationStatus.SUCCESS

        return result

    except Exception as exc:  # noqa: BLE001
        return ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=face_index,
            requested_mm=distance_mm,
            error_message=str(exc),
        )

    finally:
        FreeCAD.closeDocument(doc.Name)