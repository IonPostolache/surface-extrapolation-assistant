"""Day 3 — batch extrapolation over all boundary faces.

This module ties together:
    - step_io.load_step / load_boundary
    - topology.get_boundary_faces
    - extrapolation.extrapolate_face

...into a single batch operation that:
    1. Loads a surface and a boundary curve
    2. Identifies boundary faces
    3. Extrapolates each face
    4. Collects results (successes, partials, failures)
    5. Returns a structured BatchReport

One failing face does NOT abort the batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore

from surface_assistant.extrapolation import (
    ExtrapolationResult,
    ExtrapolationStatus,
    extrapolate_face,
)
from surface_assistant.step_io import load_step, load_boundary
from surface_assistant.topology import BoundaryFace, get_boundary_faces


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class BatchReport:
    """Aggregated outcome of a batch extrapolation run."""

    step_file: str
    boundary_file: str
    target_mm: float
    tolerance_percent: float

    total_faces: int = 0
    results: list[ExtrapolationResult] = field(default_factory=list)

    # Populated after the run
    extended_faces: list[Part.Face] = field(default_factory=list)

    @property
    def successes(self) -> list[ExtrapolationResult]:
        return [r for r in self.results
                if r.status == ExtrapolationStatus.SUCCESS]

    @property
    def partials(self) -> list[ExtrapolationResult]:
        return [r for r in self.results
                if r.status == ExtrapolationStatus.PARTIAL]

    @property
    def failures(self) -> list[ExtrapolationResult]:
        return [r for r in self.results
                if r.status == ExtrapolationStatus.FAILED]

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        return len(self.successes) / len(self.results) * 100.0

    def summary(self) -> str:
        lines = [
            f"Batch report for {Path(self.step_file).name}",
            f"  Target distance : {self.target_mm} mm "
            f"(+/- {self.tolerance_percent}%)",
            f"  Total faces     : {self.total_faces}",
            f"  Successes       : {len(self.successes)} "
            f"({self.success_rate:.1f}%)",
            f"  Partials        : {len(self.partials)}",
            f"  Failures        : {len(self.failures)}",
            "",
            "Per-face results:",
        ]
        for r in self.results:
            lines.append(f"  {r.short()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(
    step_file: str | Path,
    boundary_file: str | Path,
    *,
    target_mm: float = 100.0,
    tolerance_percent: float = 2.0,
    direction: str = "all",
    max_correction_passes: int = 1,
    doc_name: str = "_BatchSurface",
) -> BatchReport:
    """Run extrapolation over all boundary faces of a STEP surface.

    Parameters
    ----------
    step_file : str | Path
        Path to the surface STEP file.
    boundary_file : str | Path
        Path to the boundary curve STEP file.
    target_mm : float
        Target extrapolation distance per face, in millimetres.
    tolerance_percent : float
        Acceptable deviation from `target_mm`, as a percentage.
    direction : str
        "U+", "U-", "V+", "V-", or "all".
    max_correction_passes : int
        Correction passes per face if the first attempt misses tolerance.
    doc_name : str
        Name of the FreeCAD document that holds the source surface.

    Returns
    -------
    BatchReport
        Aggregated results for the whole surface.
    """

    report = BatchReport(
        step_file=str(step_file),
        boundary_file=str(boundary_file),
        target_mm=target_mm,
        tolerance_percent=tolerance_percent,
    )

    doc_surface = None
    doc_boundary = None

    try:
        doc_surface, shape = load_step(step_file, doc_name=doc_name)
        doc_boundary, boundary = load_boundary(
            boundary_file, doc_name="_BoundaryCurve"
        )

        boundary_faces: list[BoundaryFace] = get_boundary_faces(shape, boundary)
        report.total_faces = len(boundary_faces)

        for bf in boundary_faces:
            result = extrapolate_face(
                face=bf.face,
                distance_mm=target_mm,
                direction=direction,  # type: ignore[arg-type]
                tolerance_percent=tolerance_percent,
                max_correction_passes=max_correction_passes,
                face_index=bf.index,
            )
            report.results.append(result)

            if result.status in (
                ExtrapolationStatus.SUCCESS,
                ExtrapolationStatus.PARTIAL,
            ) and result.extended_face is not None:
                report.extended_faces.append(result.extended_face)

        return report

    finally:
        for doc in (doc_boundary, doc_surface):
            if doc is None:
                continue
            try:
                FreeCAD.closeDocument(doc.Name)
            except Exception:
                # Document already closed or never created — ignore.
                pass