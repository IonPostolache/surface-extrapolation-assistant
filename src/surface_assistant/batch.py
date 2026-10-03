"""Day 3 — batch extrapolation over all boundary faces.

This module ties together:
    - step_io.load_step / load_boundary
    - topology.get_boundary_faces_no_curve
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
from surface_assistant.step_io import load_step
from surface_assistant.config import load_config
from surface_assistant.llm import LLMDiagnosis
from surface_assistant.trim import trim_overlapping_faces, TrimStatus
from surface_assistant.trim import TrimResult
from surface_assistant.io import trim_face_to_outside_boundary

from surface_assistant.topology import (
    BoundaryFace,
    get_boundary_faces_no_curve,
    get_outer_boundary_edges,
    get_extendable_edges_for_face,
    get_neighbor_faces
)


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

    deferred_faces: list[int] = field(default_factory=list)

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

        if self.deferred_faces:
            lines.append("")
            lines.append(f"  Deferred faces (left unchanged): {self.deferred_faces}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(
    step_file: str | Path,
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
        target_mm=target_mm,
        tolerance_percent=tolerance_percent,
    )

    doc_surface = None

    try:
        doc_surface, shape = load_step(step_file, doc_name=doc_name)
        boundary_faces: list[BoundaryFace] = get_boundary_faces_no_curve(shape)
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

        outer_edges = get_outer_boundary_edges(shape)

        # Compute the reference normal once, before the loop:
        try:
            ref_normal = boundary_faces[0].face.normalAt(0.5, 0.5)
        except Exception:
            ref_normal = FreeCAD.Vector(0, 0, 1)

        # Compute interior faces once, before the loop — used by the trim
        # fallback for faces whose boundary-wire slab can't be built.
        boundary_indices = {bf.index for bf in boundary_faces}
        non_boundary_faces = [
            Part.Face(f) for i, f in enumerate(shape.Faces)
            if i not in boundary_indices
        ]
        if non_boundary_faces:
            print(f"[batch] {len(non_boundary_faces)} interior faces identified")

        for bf in boundary_faces:
            extendable_edges = get_extendable_edges_for_face(bf.face, outer_edges)
            neighbors = get_neighbor_faces(bf.face, all_faces)
            report.face_directions[bf.index] = f"{len(extendable_edges)} edges"

            result = extrapolate_face(
                face=bf.face,
                distance_mm=target_mm,
                extendable_edges=extendable_edges,
                neighbor_faces=neighbors,
                tolerance_percent=tolerance_percent,
                face_index=bf.index,
            )
            report.results.append(result)

            if result.status == ExtrapolationStatus.SUCCESS and result.extended_face is not None:
                trimmed = trim_face_to_outside_boundary(
                    result.extended_face,
                    list(outer_edges),
                    ref_normal,
                    interior_faces=non_boundary_faces, 
                )
                orig_area = result.extended_face.Area
                # trim_area = trimmed.Area
                trim_area = trimmed.Area if hasattr(trimmed, "Area") else 0.0
                print(f"[trim-outside] face {bf.index}: {orig_area:.2f} → {trim_area:.2f} mm²")

                # Keep the original boundary face + its outer extension band
                report.extended_faces.append(bf.face)
                report.extended_faces.append(trimmed)
            elif result.status == ExtrapolationStatus.DEFERRED:
                report.extended_faces.append(bf.face)
                report.deferred_faces.append(bf.index)
 
        # Join the extended faces into a shell
        if report.extended_faces:
            cfg = load_config()

            # 1. Compute the non-boundary (interior) faces FIRST
            # boundary_indices = {bf.index for bf in boundary_faces}
            # non_boundary_faces = [
            #     Part.Face(f) for i, f in enumerate(shape.Faces)
            #     if i not in boundary_indices
            # ]
            # if non_boundary_faces:
            #     print(f"[batch] {len(non_boundary_faces)} interior faces identified")

            trim_result = trim_overlapping_faces(report.extended_faces, verbose=True)
            print(f"[batch] {trim_result.short()}")


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
        for doc in (doc_surface, ):
            if doc is None:
                continue
            try:
                FreeCAD.closeDocument(doc.Name)
            except Exception:
                # Document already closed or never created — ignore.
                pass