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
from surface_assistant.topology import BoundaryFace, estimate_free_to_shared_distance, get_boundary_faces
from surface_assistant.topology import infer_uv_directions, UVDirections
from surface_assistant.config import load_config
from surface_assistant.llm import LLMDiagnosis
from surface_assistant.trim import trim_overlapping_faces, TrimStatus
from surface_assistant.trim import TrimResult
from surface_assistant.topology import get_extendable_edges


from surface_assistant.join import (
    JoinResult,
    JoinStatus,
    join_faces,
    _count_open_edges,
)

def _is_valid_face(face: Part.Face, min_area: float = 1e-6) -> bool:
    """Return True if the face has non-degenerate geometry."""
    try:
        if face.Area < min_area:
            return False
        bb = face.BoundBox
        # A void box has infinite extents
        if not (bb.XLength > 0 or bb.YLength > 0 or bb.ZLength > 0):
            return False
        return True
    except Exception:
        return False


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

    trim_result: "TrimResult | None" = None

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
        if self.trim_result is not None:
            lines.append("")
            lines.append(f"  {self.trim_result.short()}")
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

        boundary_indices = {bf.index for bf in boundary_faces}
        interior_faces_for_classification = [
            Part.Face(f) for i, f in enumerate(shape.Faces)
            if i not in boundary_indices
        ]

        all_faces = (
            [bf.face for bf in boundary_faces]
            + interior_faces_for_classification
        )

        for bf in boundary_faces:
            dirs = infer_uv_directions(
                bf.face, all_faces, boundary,
                tolerance=1e-3, boundary_tolerance=0.5,
            )
            report.face_directions[bf.index] = dirs.describe()

            extendable_edges = get_extendable_edges(
                bf.face, all_faces, boundary,
                tolerance=1e-3, boundary_tolerance=0.5,
            )

            local_boundary_distance = estimate_free_to_shared_distance(bf.face, all_faces)
            effective_distance = min(target_mm, local_boundary_distance * 0.9)

            result = extrapolate_face(
                face=bf.face,
                distance_mm=effective_distance,
                directions=dirs,
                extendable_edges=extendable_edges,
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

            # 1. Compute the non-boundary (interior) faces FIRST
            boundary_indices = {bf.index for bf in boundary_faces}
            non_boundary_faces = [
                Part.Face(f) for i, f in enumerate(shape.Faces)
                if i not in boundary_indices
            ]
            if non_boundary_faces:
                print(f"[batch] {len(non_boundary_faces)} interior faces identified")

            # 2. Trim overlaps between extended faces (small set)
            trim_result = trim_overlapping_faces(report.extended_faces, verbose=True)
            print(f"[batch] {trim_result.short()}")

            # 3. Trim extended faces where they overlap interior faces
            from surface_assistant.trim import trim_against_interior
            if non_boundary_faces and (
                len(trim_result.trimmed_faces) * len(non_boundary_faces) < 500
            ):
                trimmed_extended = trim_against_interior(
                    trim_result.trimmed_faces,
                    non_boundary_faces,
                    tolerance_mm=cfg.join.sewing_tolerance_mm,
                    verbose=True,
                )
                trim_result.trimmed_faces = trimmed_extended
            else:
                print(
                    f"[batch] skipping interior trim "
                    f"({len(trim_result.trimmed_faces)} × {len(non_boundary_faces)} pairs)"
                )

            report.extended_faces = trim_result.trimmed_faces
            report.trim_result = trim_result

            # 4. Fuse the extended faces (small set) into a shell
            extended_join = join_faces(
                trim_result.trimmed_faces,
                tolerance_mm=cfg.join.sewing_tolerance_mm,
                refine=cfg.join.refine_shape,
            )

            # 5. Combine extended + interior into one compound
            #    Works whether or not the extended faces fused into a shell.
            if non_boundary_faces:
                valid_interior = [f for f in non_boundary_faces if _is_valid_face(f)]
                filtered = len(non_boundary_faces) - len(valid_interior)
                if filtered > 0:
                    print(f"[batch] filtered {filtered} degenerate faces")

                try:
                    # Use whatever extended shape we have:
                    #   - sewed_shell if the fuse succeeded
                    #   - otherwise, the individual trimmed faces
                    if extended_join.sewed_shell is not None:
                        extended_shape = extended_join.sewed_shell
                        extended_count = len(trim_result.trimmed_faces)
                    else:
                        extended_shape = Part.makeCompound(trim_result.trimmed_faces)
                        extended_count = len(trim_result.trimmed_faces)

                    combined = Part.makeCompound([extended_shape] + valid_interior)
                    extended_join.sewed_shell = combined
                    extended_join.open_edge_count = 0
                    extended_join.input_face_count = extended_count + len(valid_interior)
                    extended_join.method_used = "compound"
                    extended_join.status = JoinStatus.SUCCESS
                except Exception as exc:
                    print(f"[batch] compound failed: {exc}")

            report.join_result = extended_join

            # Save the FCStd if a path was given
            if output_fcstd is not None:
                try:
                    from surface_assistant.io import save_extended_faces
                    save_extended_faces(report, Path(output_fcstd))
                except Exception as exc:  # noqa: BLE001
                    print(f"[batch] FCStd save failed: {exc}")

            # LLM diagnosis on failure
            if use_llm and report.join_result.status != JoinStatus.SUCCESS:
                from surface_assistant.llm import diagnose

                png_path = None
                if output_fcstd is not None:
                    try:
                        png_path = Path(output_fcstd).with_suffix(".png")
                        from surface_assistant.io import render_fcstd_to_png_subprocess
                        success = render_fcstd_to_png_subprocess(
                            Path(output_fcstd), png_path
                        )
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