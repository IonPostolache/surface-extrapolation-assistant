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
from surface_assistant.join import JoinResult, join_faces, JoinStatus
from surface_assistant.topology import infer_uv_directions, UVDirections
from surface_assistant.config import load_config
from surface_assistant.llm import LLMDiagnosis


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

    join_result: JoinResult | None = None

    face_directions: dict[int, str] = field(default_factory=dict)

    llm_diagnosis: "LLMDiagnosis | None" = None

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
        if self.join_result is not None:
            lines.append("")
            lines.append(f"  {self.join_result.short()}")
        if self.llm_diagnosis is not None:
            lines.append("")
            lines.append("  LLM diagnosis:")
            lines.append(f"    {self.llm_diagnosis.diagnosis}")
            lines.append(f"    confidence: {self.llm_diagnosis.confidence:.2f}")
            if self.llm_diagnosis.recommended_actions:
                actions = ", ".join(self.llm_diagnosis.recommended_actions)
                lines.append(f"    actions: {actions}")
            else:
                lines.append("    actions: none")
        for r in self.results:
            dir_str = self.face_directions.get(r.face_index, "?")
            lines.append(f"  {r.short()}  [dirs: {dir_str}]")

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
    use_llm: bool = False, 
    output_fcstd: Path | None = None,
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
        all_faces = [bf.face for bf in boundary_faces]


        for bf in boundary_faces:
            # Infer which UV sides of this face touch the free boundary
            dirs = infer_uv_directions(bf.face, all_faces, boundary, tolerance=1e-3, boundary_tolerance=0.5)
            report.face_directions[bf.index] = dirs.describe()

            result = extrapolate_face(
                face=bf.face,
                distance_mm=target_mm,
                directions=dirs,
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

        # Join the extended faces into a shell
        if report.extended_faces:
            cfg = load_config()
            report.join_result = join_faces(
                report.extended_faces,
                tolerance_mm=cfg.join.sewing_tolerance_mm,
                refine=cfg.join.refine_shape,
            )

            # Save the FCStd if a path was given, so the renderer can open it
            if output_fcstd is not None:
                try:
                    from surface_assistant.io import save_extended_faces
                    save_extended_faces(report, Path(output_fcstd))
                except Exception as exc:  # noqa: BLE001
                    print(f"[batch] FCStd save failed: {exc}")

            # If the join failed and the LLM is enabled, diagnose
            if use_llm and report.join_result.status != JoinStatus.SUCCESS:
                from surface_assistant.llm import diagnose
                from surface_assistant.io import render_fcstd_to_png

                png_path = None
                if output_fcstd is not None:
                    try:
                        png_path = Path(output_fcstd).with_suffix(".png")
                        from surface_assistant.io import render_fcstd_to_png_subprocess
                        success = render_fcstd_to_png_subprocess(Path(output_fcstd), png_path)
                        if not success:
                            png_path = None
                    except Exception as exc:
                        print(f"[batch] PNG render failed: {exc}")
                        png_path = None

                report.llm_diagnosis = diagnose(
                    report.join_result,
                    report.results,
                    image_path=png_path,
                )

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