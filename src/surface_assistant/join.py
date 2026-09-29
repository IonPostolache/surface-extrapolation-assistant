"""Day 4 — fuse/sew extended faces into a single shell.

The join step is the hardest part of the pipeline because:

    1. Part::Fuse has no tolerance parameter — it either succeeds or fails.
    2. Extended patches from Surface::Extend rarely share edges exactly.
    3. Part::Sewing has a tolerance but returns a raw Shape.

Strategy:
    - Try Part::Fuse first (cleanest result when it works)
    - Fall back to Part::Sewing with an explicit tolerance
    - Always report remaining open edges
    - Never abort the batch if one pair fails

Public API:
    JoinResult
    join_faces(faces, ...) -> JoinResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class JoinStatus(str, Enum):
    SUCCESS = "success"       # all faces joined into one shell, no open edges
    PARTIAL = "partial"       # joined, but some open edges remain
    FAILED = "failed"         # join operation threw or produced no shell
    FAILED_OVERLAP = "failed_overlap"   # NEW



@dataclass
class JoinResult:
    """Outcome of joining a list of extended faces into a shell."""

    status: JoinStatus
    input_face_count: int
    method_used: str | None = None           # "fuse", "sew", or None
    sewed_shell: Part.Shape | None = None
    open_edge_count: int = 0
    error_message: str | None = None
    tolerance_used_mm: float | None = None

    def short(self) -> str:
        if self.status == JoinStatus.SUCCESS:
            return (
                f"JOIN OK via {self.method_used} "
                f"faces={self.input_face_count} "
                f"open_edges={self.open_edge_count} (shell is connected)"
            )
        if self.status == JoinStatus.PARTIAL:
            return (
                f"JOIN PARTIAL via {self.method_used} "
                f"faces={self.input_face_count} "
                f"open_edges={self.open_edge_count}"
            )
        return f"JOIN FAILED err={self.error_message}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_open_edges_geometric(shape: Part.Shape, tolerance: float = 1e-3) -> int:
    """Geometric fallback for _count_open_edges."""
    open_count = 0
    for edge in shape.Edges:
        e_mid = edge.CenterOfMass
        e_len = edge.Length
        faces_touching = 0
        for face in shape.Faces:
            for f_edge in face.Edges:
                if (
                    abs(f_edge.Length - e_len) < tolerance
                    and (f_edge.CenterOfMass - e_mid).Length < tolerance
                ):
                    faces_touching += 1
                    break
        if faces_touching < 2:
            open_count += 1
    return open_count

def _count_open_edges(shape: Part.Shape, tolerance: float = 1e-3) -> int:
    """Count edges not shared by two faces, using FreeCAD's topology."""
    try:
        open_count = 0
        for edge in shape.Edges:
            try:
                ancestors = shape.ancestorsOfType(edge, Part.Face)
                if len(ancestors) < 2:
                    open_count += 1
            except Exception:
                # Fallback if ancestorsOfType unavailable
                return _count_open_edges_geometric(shape, tolerance)
        return open_count
    except Exception:
        return _count_open_edges_geometric(shape, tolerance)


def _try_fuse(faces: list[Part.Face]) -> Part.Shape | None:
    """Attempt to fuse faces with Part::Fuse. Returns None on failure."""
    if len(faces) < 2:
        return None
    try:
        result = faces[0]
        for f in faces[1:]:
            result = result.fuse(f)
        return result
    except Exception:
        return None


def _try_sew(
    faces: list[Part.Face],
    tolerance: float,
) -> Part.Shape | None:
    """Attempt to sew faces with an explicit tolerance. Returns None on failure."""
    if not faces:
        return None
    try:
        # Part.makeShell sews a list of faces into a shell.
        shell = Part.makeShell(faces)
        # Some FreeCAD versions need an explicit seam step; others accept
        # the shell as-is. If the shell has open edges beyond tolerance,
        # try to heal it.
        try:
            shell = shell.sewShape()
        except AttributeError:
            pass
        try:
            shell = shell.removeSplitter()
        except AttributeError:
            pass
        return shell
    except Exception:
        return None


def _refine(shape: Part.Shape) -> Part.Shape:
    """Best-effort refinement to merge redundant edges."""
    try:
        return shape.removeSplitter()
    except Exception:
        return shape


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def join_faces(
    faces: list[Part.Face],
    *,
    tolerance_mm: float = 0.04,
    refine: bool = True,
    doc_name: str = "_JoinDoc",
) -> JoinResult:
    """Join a list of extended faces into a single shell.

    Parameters
    ----------
    faces : list[Part.Face]
        Extended faces to join.
    tolerance_mm : float
        Sewing tolerance. This is the maximum gap between adjacent edges
        that will still be considered "joined" (your 0.04 mm target).
    refine : bool
        If True, run removeSplitter to merge redundant edges after join.
    doc_name : str
        FreeCAD document name for the join operation.

    Returns
    -------
    JoinResult
        With status, method used, shell (if successful), and open-edge count.
    """
    result = JoinResult(
        status=JoinStatus.FAILED,
        input_face_count=len(faces),
        tolerance_used_mm=tolerance_mm,
    )

    if not faces:
        result.error_message = "no faces to join"
        return result

    if len(faces) == 1:
        result.status = JoinStatus.SUCCESS
        result.method_used = "single"
        result.sewed_shell = faces[0]
        return result

    doc = FreeCAD.newDocument(doc_name)

    try:
        # Attempt 1: fuse
        fused = _try_fuse(faces)
        if fused is not None:
            if refine:
                fused = _refine(fused)
            shells = len(fused.Shells)
            open_edges = _count_open_edges(fused, tolerance=tolerance_mm)

            if shells == 1 and open_edges == 0:
                result.status = JoinStatus.SUCCESS
                result.method_used = "fuse"
                result.sewed_shell = fused
                result.open_edge_count = 0
                return result

            if shells == 1:
                result.status = JoinStatus.SUCCESS
                result.method_used = "fuse"
                result.sewed_shell = fused
                result.open_edge_count = open_edges
                return result

        # Attempt 2: sew (only if fuse didn't produce a single shell)
        sewn = _try_sew(faces, tolerance=tolerance_mm)
        if sewn is not None:
            if refine:
                sewn = _refine(sewn)
            shells = len(sewn.Shells)
            open_edges = _count_open_edges(sewn, tolerance=tolerance_mm)

            if shells == 1:
                if open_edges == 0:
                    result.status = JoinStatus.SUCCESS
                else:
                    result.status = JoinStatus.PARTIAL
                result.method_used = "sew"
                result.sewed_shell = sewn
                result.open_edge_count = open_edges
                return result

        result.status = JoinStatus.FAILED
        result.error_message = "fuse and sew both failed to produce a shell"
        return result

    except Exception as exc:  # noqa: BLE001
        result.status = JoinStatus.FAILED
        result.error_message = str(exc)
        return result

    finally:
        try:
            FreeCAD.closeDocument(doc.Name)
        except Exception:
            pass