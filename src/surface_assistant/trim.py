"""Split over-extended faces at their neighbor boundaries.

When extending boundary faces, adjacent faces sometimes extend into each
other's territory, producing overlaps that cannot be joined cleanly.

This module:
    1. Identifies pairs of extended faces that overlap.
    2. For each pair, extracts the intersection curve between them.
    3. Uses the curve to build a cutting surface.
    4. Slices the over-extended face with the cutting surface.
    5. Keeps the piece that does NOT overlap the neighbor.

The result is a set of trimmed faces that meet cleanly at their
original shared edges, ready for joining.
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

class TrimStatus(str, Enum):
    SUCCESS = "success"           # no overlaps needed trimming, or all trimmed
    PARTIAL = "partial"           # some trims succeeded, some failed
    FAILED = "failed"             # trimming failed entirely
    NO_OVERLAP = "no_overlap"     # no overlaps detected


@dataclass
class TrimResult:
    """Outcome of a trim pass over extended faces."""

    status: TrimStatus
    input_face_count: int
    trimmed_face_count: int = 0
    overlap_pairs_found: int = 0
    overlaps_resolved: int = 0
    trimmed_faces: list[Part.Face] = field(default_factory=list)
    error_message: str | None = None

    def short(self) -> str:
        if self.status == TrimStatus.NO_OVERLAP:
            return f"TRIM no_overlap faces={self.input_face_count}"
        if self.status == TrimStatus.SUCCESS:
            return (
                f"TRIM OK faces={self.input_face_count} "
                f"pairs={self.overlap_pairs_found} "
                f"resolved={self.overlaps_resolved}"
            )
        if self.status == TrimStatus.PARTIAL:
            return (
                f"TRIM PARTIAL faces={self.input_face_count} "
                f"pairs={self.overlap_pairs_found} "
                f"resolved={self.overlaps_resolved}"
            )
        return f"TRIM FAILED err={self.error_message}"


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

def _bounding_boxes_overlap(a: Part.Face, b: Part.Face, slack: float = 0.5) -> bool:
    """Cheap check: do the two faces' bounding boxes intersect?"""
    try:
        bb_a = a.BoundBox
        bb_b = b.BoundBox
        # Expand slightly to avoid float equality issues
        bb_a.XMin -= slack; bb_a.YMin -= slack; bb_a.ZMin -= slack
        bb_a.XMax += slack; bb_a.YMax += slack; bb_a.ZMax += slack
        return bb_a.intersect(bb_b)
    except Exception:
        return False


def _faces_overlap(a: Part.Face, b: Part.Face) -> bool:
    """Exact check: do the two faces geometrically intersect?"""
    try:
        common = a.common(b)
        # If the common shape has any faces or edges with non-trivial area/length,
        # the two faces overlap.
        if not common.Faces and not common.Edges:
            return False
        if common.Area > 1e-8:
            return True
        # Faces touching only at an edge: not an overlap we need to fix.
        return False
    except Exception:
        return False


def _find_overlap_pairs(faces: list[Part.Face]) -> list[tuple[int, int]]:
    """Return pairs of face indices whose extensions overlap."""
    pairs: list[tuple[int, int]] = []
    n = len(faces)
    for i in range(n):
        for j in range(i + 1, n):
            if not _bounding_boxes_overlap(faces[i], faces[j]):
                continue
            try:
                common = faces[i].common(faces[j])
                # Require only a small non-zero area — corner overlaps
                # are often a few square mm.
                if common.Area > 1e-3:
                    pairs.append((i, j))
            except Exception:
                continue
    return pairs


# ---------------------------------------------------------------------------
# Trimming
# ---------------------------------------------------------------------------

def _build_cutting_tool(
    base_face: Part.Face,
    neighbor_face: Part.Face,
    extension: float = 50.0,
) -> Part.Shape | None:
    """Build a surface that slices through base_face at the neighbor's boundary.

    Strategy: take the intersection curve between the two faces, then
    extrude it perpendicular to base_face to make a cutting surface.

    NOTE: this derives the cutter from two *already-extended* faces, which
    is fragile at rounded corners where both sides may already be
    malformed (see _build_cutting_tool_from_edge for the preferred path
    when the original, pre-extension boundary geometry is available).
    """
    try:
        # Intersection curve between the two extensions
        section = base_face.section(neighbor_face)
        if not section.Edges:
            return None

        # Build a wire from the section edges
        try:
            wire = Part.Wire(section.Edges)
        except Exception:
            # If the section isn't a single wire, use the compound
            wire = Part.Compound(section.Edges)

        # Compute a cut direction: the normal of base_face at its center.
        try:
            normal = base_face.normalAt(0.5, 0.5)
        except Exception:
            normal = FreeCAD.Vector(0, 0, 1)

        # Extrude both directions so the tool fully crosses base_face
        tool_pos = wire.extrude(normal.multiply(extension))
        tool_neg = wire.extrude(normal.multiply(-extension))
        return tool_pos.fuse(tool_neg)
    except Exception:
        return None


def _build_cutting_tool_from_edge(
    base_face: Part.Face,
    original_edge: Part.Edge,
    extension: float = 50.0,
) -> Part.Shape | None:
    """Build a cutting tool from a known-good, pre-extension boundary edge.

    This is the preferred cutter for trimming an over-extended face back
    to a neighbor's territory: it uses the *original* shared edge (known
    to be correct, since it came straight from the imported STEP geometry)
    rather than an intersection between two already-extended, possibly
    malformed surfaces. Critical at rounded corners, where a
    post-extension section() between two untrimmed/square-cornered
    surfaces can be empty, multi-segment, or simply wrong.

    Extrudes the edge far enough along base_face's local normal (both
    directions) to guarantee the cutter fully crosses the over-extended
    face.
    """
    try:
        wire = Part.Wire([original_edge])
    except Exception:
        wire = original_edge

    try:
        # Use the normal near the edge's own midpoint rather than the
        # face center — more representative on curved faces where the
        # normal varies significantly across the surface.
        mid = original_edge.CenterOfMass
        try:
            u, v = base_face.Surface.parameter(mid)
            normal = base_face.normalAt(u, v)
        except Exception:
            normal = base_face.normalAt(0.5, 0.5)
    except Exception:
        normal = FreeCAD.Vector(0, 0, 1)

    try:
        tool_pos = wire.extrude(normal.multiply(extension))
        tool_neg = wire.extrude(normal.multiply(-extension))
        return tool_pos.fuse(tool_neg)
    except Exception:
        return None


def _slice_face(face: Part.Face, tool: Part.Shape) -> list[Part.Face]:
    """Slice `face` with `tool`. Returns the resulting pieces."""
    try:
        # Part::Slice via the BOPTools scripting API
        import BOPTools.SplitAPI
        result = BOPTools.SplitAPI.slice(
            face, [tool], "Standard", 0.0
        )
        return list(result.Faces)
    except Exception:
        # Fallback: boolean cut to remove the overlapping region,
        # then split at the tool's intersection.
        try:
            cut = face.cut(tool)
            return list(cut.Faces)
        except Exception:
            return [face]


def _keep_piece_away_from_neighbor(
    pieces: list[Part.Face],
    neighbor: Part.Face,
) -> Part.Face:
    """Select the piece that does not overlap the neighbor."""
    if len(pieces) == 1:
        return pieces[0]

    best = pieces[0]
    best_overlap = None
    for p in pieces:
        try:
            common = p.common(neighbor)
            overlap_area = common.Area if common.Faces else 0.0
        except Exception:
            overlap_area = 0.0

        if best_overlap is None or overlap_area < best_overlap:
            best_overlap = overlap_area
            best = p

    return best


def trim_overlapping_faces(
    faces: list[Part.Face],
    *,
    max_iterations: int = 5,
    verbose: bool = False,
) -> TrimResult:
    """Detect and trim overlapping extended faces.

    Parameters
    ----------
    faces : list[Part.Face]
        Extended faces from the extrapolation step.
    max_iterations : int
        How many trim passes to attempt. Each pass may resolve more
        overlaps as faces are reshaped.
    verbose : bool
        Print progress details.

    Returns
    -------
    TrimResult
    """
    result = TrimResult(
        status=TrimStatus.FAILED,
        input_face_count=len(faces),
    )

    if len(faces) < 2:
        result.status = TrimStatus.NO_OVERLAP
        result.trimmed_faces = list(faces)
        return result

    current_faces = list(faces)

    for iteration in range(max_iterations):
        pairs = _find_overlap_pairs(current_faces)
        if not pairs:
            break

        if verbose:
            print(f"[trim] iteration {iteration}: {len(pairs)} overlap(s)")

        result.overlap_pairs_found = max(result.overlap_pairs_found, len(pairs))

        resolved_this_pass = 0
        for i, j in pairs:
            base = current_faces[i]
            neighbor = current_faces[j]

            tool = _build_cutting_tool(base, neighbor)
            if tool is None:
                print(f"[trim] pair ({i},{j}): could not build cutting tool")
                continue
            print(f"[trim] pair ({i},{j}): tool has {len(tool.Faces)} faces, {len(tool.Edges)} edges")

            pieces = _slice_face(base, tool)
            if len(pieces) < 2:
                continue

            kept = _keep_piece_away_from_neighbor(pieces, neighbor)
            current_faces[i] = kept
            resolved_this_pass += 1

        result.overlaps_resolved += resolved_this_pass

        if resolved_this_pass == 0:
            break

    # Final overlap check
    final_pairs = _find_overlap_pairs(current_faces)

    if not final_pairs:
        result.status = (
            TrimStatus.NO_OVERLAP if result.overlap_pairs_found == 0
            else TrimStatus.SUCCESS
        )
    elif result.overlaps_resolved > 0:
        result.status = TrimStatus.PARTIAL
    else:
        result.status = TrimStatus.FAILED
        result.error_message = "trimming did not resolve any overlaps"

    result.trimmed_faces = current_faces
    result.trimmed_face_count = len(current_faces)
    return result


def trim_to_original_neighbor_edges(
    extended_faces: list[Part.Face],
    original_boundary_faces: list[Part.Face],
    boundary_face_to_extended_index: dict[int, int],
    extendable_edge_sets: dict[int, list],
    all_original_edges_by_face: dict[int, list],
    *,
    verbose: bool = False,
) -> list[Part.Face]:
    """Trim each extended face back using its neighbors' ORIGINAL edges.

    This is the preferred trim pass — run it before
    trim_overlapping_faces(). It avoids deriving cutters from
    already-extended geometry, which is what produces the "rounded
    corner became square" artifact: Surface::Extend can re-evaluate a
    rounded trim wire against a larger parametric domain and lose the
    fillet, and trimming two such faces against *each other* just
    propagates the error. Here the cutter is always a known-good edge
    straight from the imported STEP data.

    Parameters
    ----------
    extended_faces : list[Part.Face]
        The faces coming out of extrapolate_face, in boundary-face order.
    original_boundary_faces : list[Part.Face]
        The ORIGINAL (pre-extension) boundary faces, same order as
        extended_faces.
    boundary_face_to_extended_index : dict[int, int]
        Maps a boundary face's topology index -> its position in
        extended_faces / original_boundary_faces.
    extendable_edge_sets : dict[int, list]
        Per boundary-face-index, the edges that were extended (from
        topology.get_extendable_edges). Used to skip trimming the sides
        that were just extended — only the SHARED sides need trimming.
    all_original_edges_by_face : dict[int, list]
        Per boundary-face-index, all of that face's ORIGINAL edges
        (shared + free), used to find shared edges with each neighbor.

    Returns
    -------
    list[Part.Face]
        Trimmed extended faces, same order/length as the input.
    """
    result = list(extended_faces)
    n = len(result)

    indices = list(boundary_face_to_extended_index.keys())

    for a_idx in indices:
        a_pos = boundary_face_to_extended_index[a_idx]
        extended_a = result[a_pos]
        extendable_a = extendable_edge_sets.get(a_idx, [])

        for b_idx in indices:
            if a_idx == b_idx:
                continue
            b_pos = boundary_face_to_extended_index[b_idx]

            # Find the edge(s) ORIGINALLY shared between face a and face b.
            edges_a = all_original_edges_by_face.get(a_idx, [])
            edges_b = all_original_edges_by_face.get(b_idx, [])
            shared_edge = None
            for ea in edges_a:
                if any(e is ea for e in extendable_a):
                    continue  # this edge was extended, not shared — skip
                for eb in edges_b:
                    if _edges_match(ea, eb, tol=1e-3):
                        shared_edge = ea
                        break
                if shared_edge is not None:
                    break

            if shared_edge is None:
                continue  # a and b are not original neighbors

            tool = _build_cutting_tool_from_edge(extended_a, shared_edge)
            if tool is None:
                if verbose:
                    print(f"[trim-orig] ({a_idx},{b_idx}): could not build cutter")
                continue

            pieces = _slice_face(extended_a, tool)
            if len(pieces) < 2:
                continue

            # Keep the piece that still contains the face's own original
            # footprint, i.e. does NOT drift into the neighbor's original
            # territory.
            kept = _keep_piece_away_from_neighbor(
                pieces, original_boundary_faces[b_pos]
            )
            extended_a = kept
            if verbose:
                print(f"[trim-orig] ({a_idx},{b_idx}): trimmed against original edge")

        result[a_pos] = extended_a

    return result


def _edges_match(edge_a: Part.Edge, edge_b: Part.Edge, tol: float = 1e-3) -> bool:
    """Loose edge equality by endpoints — duplicated locally to avoid a
    circular import with topology.py."""
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


def trim_against_interior(
    extended_faces: list[Part.Face],
    interior_faces: list[Part.Face],
    *,
    tolerance_mm: float = 0.04,
    verbose: bool = False,
) -> list[Part.Face]:
    """Trim each extended face where it overlaps an interior face.

    Extended faces are sliced; interior faces are never modified.
    """
    trimmed = []
    for i, ext_face in enumerate(extended_faces):
        current = ext_face
        for j, interior in enumerate(interior_faces):
            if not _bbox_substantially_overlaps(current, interior, min_overlap_mm=1.0):
                continue
            if verbose:
                print(f"[trim-interior] ext[{i}] overlaps interior[{j}]")

            tool = _build_cutting_tool(current, interior, extension=50.0)
            if tool is None:
                continue

            pieces = _slice_face(current, tool)
            if len(pieces) < 2:
                continue

            # Keep the piece that does NOT overlap the interior
            current = _keep_piece_away_from_neighbor(pieces, interior)

        trimmed.append(current)

    return trimmed

def _bbox_substantially_overlaps(a, b, min_overlap_mm=2.0) -> bool:
    try:
        bb_a = a.BoundBox
        bb_b = b.BoundBox
        ox = min(bb_a.XMax, bb_b.XMax) - max(bb_a.XMin, bb_b.XMin)
        oy = min(bb_a.YMax, bb_b.YMax) - max(bb_a.YMin, bb_b.YMin)
        oz = min(bb_a.ZMax, bb_b.ZMax) - max(bb_a.ZMin, bb_b.ZMin)
        # Require meaningful overlap on all axes
        if ox < min_overlap_mm or oy < min_overlap_mm or oz < min_overlap_mm:
            return False
        return True
    except Exception:
        return False