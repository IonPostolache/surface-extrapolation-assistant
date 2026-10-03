"""Save extended faces and join results to .FCStd and .STEP files."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# isort: off
from surface_assistant import freecad_setup  # noqa: F401
import FreeCAD  # type: ignore
import Part  # type: ignore
# isort: on


def save_extended_faces(report, output_path: Path) -> Path:
    """Save extended faces from a BatchReport to a .FCStd file.

    If the path ends in .step or .stp, the faces are also exported
    as STEP. Otherwise only the FCStd document is written.
    """
    output_path = Path(output_path)
    doc = FreeCAD.newDocument("_SavedOutput")

    for i, face in enumerate(report.extended_faces):
        obj = doc.addObject("Part::Feature", f"Extended_{i}")
        obj.Shape = face

    if report.join_result and report.join_result.sewed_shell is not None:
        joined = doc.addObject("Part::Feature", "JoinedShell")
        joined.Shape = report.join_result.sewed_shell

    doc.recompute()
    doc.saveAs(str(output_path))

    if output_path.suffix.lower() in (".step", ".stp"):
        Part.export(doc.Objects, str(output_path))

    return output_path


def render_fcstd_to_png(fcstd_path, output_png, views=("iso",)):
    """Render one or more FreeCAD views in an isolated GUI process."""
    output_png = Path(output_png)
    if not render_fcstd_to_png_subprocess(Path(fcstd_path), output_png, views=views):
        raise RuntimeError(f"Failed to render screenshot for {fcstd_path}")
    return output_png


def render_fcstd_to_png_subprocess(
    fcstd_path: Path,
    output_png: Path,
    size: int = 1024,
    views: tuple[str, ...] = ("iso",),
) -> bool:
    """Render selected views of an FCStd file in a separate FreeCAD GUI process."""
    fcstd_path = Path(fcstd_path).resolve()
    output_png = Path(output_png).resolve()

    snapshot_script = Path(__file__).resolve().parents[2] / "make_snapshot.py"
    if not snapshot_script.is_file():
        print(f"[render] Snapshot script not found at {snapshot_script}")
        return False

    command = [
        sys.executable,
        str(snapshot_script),
        str(fcstd_path),
        str(output_png),
        "--size",
        str(size),
        "--views",
        ",".join(views),
    ]
    if not os.environ.get("DISPLAY"):
        xvfb_run = shutil.which("xvfb-run")
        if not xvfb_run:
            print("[render] xvfb-run is required when no X display is available")
            return False
        command = [
            xvfb_run,
            "-a",
            "-s",
            "-screen 0 1280x1024x24 +extension GLX",
            *command,
        ]

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "xcb"
    xkb_config = Path("/usr/share/X11/xkb")
    if xkb_config.is_dir():
        env["XKB_CONFIG_ROOT"] = str(xkb_config)

    expected_outputs = (
        [output_png]
        if len(views) == 1
        else [
            output_png.with_name(f"{output_png.stem}_{view}{output_png.suffix}")
            for view in views
        ]
    )

    try:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0 and all(
            path.is_file() and path.stat().st_size > 0 for path in expected_outputs
        ):
            return True
        print(f"[render] Subprocess failed (code {result.returncode})")
        if result.stderr:
            print(f"[render] stderr: {result.stderr[-1000:]}")
        return False

    except subprocess.TimeoutExpired:
        print("[render] Render timed out")
        return False
    except Exception as e:
        print(f"[render] Unexpected error: {e}")
        return False

def split_by_outer_boundary(
    joined_shape: Part.Shape,
    boundary_edges: list[Part.Edge],
    extension_direction: FreeCAD.Vector | None = None,
    extension_length: float = 100.0,
) -> tuple[Part.Shape | None, Part.Shape | None]:
    """Split `joined_shape` by the wire formed by `boundary_edges`.

    The boundary wire must fully cross the shape for the split to work.
    If it's a closed loop that lies on the surface (as the initial
    surface's outer boundary typically is), we need to extrude it into
    a cutting surface first.

    Returns
    -------
    (outside_piece, inside_piece)
        The two pieces, or (None, None) on failure.
    """
    if not boundary_edges:
        return None, None

    try:
        # Build the boundary wire
        boundary_wire = Part.Wire(boundary_edges)
    except Exception:
        try:
            boundary_wire = Part.Compound(boundary_edges)
        except Exception:
            return None, None

    # Build a cutting surface from the wire
    # Use the extension direction (usually the average surface normal)
    if extension_direction is None:
        try:
            extension_direction = joined_shape.normalAt(0.5, 0.5)
        except Exception:
            extension_direction = FreeCAD.Vector(0, 0, 1)

    try:
        # Extrude the wire both ways to ensure it fully crosses the shape
        tool_pos = boundary_wire.extrude(extension_direction * extension_length)
        tool_neg = boundary_wire.extrude(-extension_direction * extension_length)
        cutting_tool = tool_pos.fuse(tool_neg)
    except Exception:
        return None, None

    # Slice
    try:
        import BOPTools.SplitAPI
        result = BOPTools.SplitAPI.slice(
            joined_shape, [cutting_tool], "Standard", 0.0
        )
        pieces = list(result.Faces) if hasattr(result, "Faces") else []
    except Exception:
        pieces = []

    if not pieces:
        # Fallback: try Shape.split
        try:
            split_result = joined_shape.split(cutting_tool)
            pieces = list(split_result.Faces) if hasattr(split_result, "Faces") else []
        except Exception:
            pieces = []

    if not pieces:
        return None, None

    # Classify pieces as inside or outside the boundary
    # "Inside" = piece whose centroid is on the side where the boundary
    # is shared with the interior faces. Simpler heuristic: piece whose
    # centroid is closer to the original surface's interior.
    # For a first pass, classify by which side the piece's bbox center sits.
    inside_piece = None
    outside_piece = None

    # Use the joined shape's own center of mass as the "interior" reference
    interior_ref = joined_shape.CenterOfMass

    # Compare each piece's distance from the interior reference to the
    # boundary
    inside_candidates = []
    outside_candidates = []
    for p in pieces:
        try:
            p_center = p.CenterOfMass
            dist_to_interior = (p_center - interior_ref).Length
            inside_candidates.append((dist_to_interior, p))
        except Exception:
            continue

    if not inside_candidates:
        return None, None

    # Sort: the piece closest to interior_ref is "inside"
    inside_candidates.sort(key=lambda t: t[0])
    inside_piece = inside_candidates[0][1]
    outside_pieces = [p for _, p in inside_candidates[1:]]

    # Combine all outside pieces
    if len(outside_pieces) == 1:
        outside_piece = outside_pieces[0]
    elif len(outside_pieces) > 1:
        try:
            outside_piece = Part.makeCompound(outside_pieces)
        except Exception:
            outside_piece = outside_pieces[0]

    return outside_piece, inside_piece


def trim_face_to_outside_boundary(
    extended_face, original_face, boundary_edges, normal,
    interior_faces=None,
    extension=200.0,
):
    if extended_face is None:
        return extended_face

    # --- Attempt 1: boundary-wire slab (precise) ---
    if boundary_edges:
        result = _trim_with_boundary_ribbon(extended_face, original_face, boundary_edges, normal)
        if result is not None:
            return result

    # --- Attempt 2: bounding-box fallback (crude but always works) ---
    if interior_faces:
        try:
            bbox = Part.makeCompound(interior_faces).BoundBox
            margin = 5.0
            box = Part.makeBox(
                bbox.XLength + 2*margin,
                bbox.YLength + 2*margin,
                bbox.ZLength + 2*margin,
                FreeCAD.Vector(bbox.XMin - margin, bbox.YMin - margin, bbox.ZMin - margin),
            )
            result = extended_face.cut(box)
            if result.Faces:
                return result
        except Exception as exc:
            print(f"[io] bbox fallback failed: {exc}")

    return extended_face


def _trim_with_boundary_ribbon(extended_face, original_face, boundary_edges, normal, ribbon_half_width=0.1):
    """Split `extended_face` by a thin ribbon swept from the boundary wire.

    Instead of building a solid slab from the boundary (which fails when
    the boundary is non-planar), we sweep the boundary wire along the
    local surface normal by ±ribbon_half_width to create a very thin
    surface ribbon. Then we use OCC's split to partition the extended
    face along that ribbon, and keep the outer piece.

    This is the FreeCAD equivalent of the CATIA technique of using a
    narrow sweep as the splitting tool for complex trimmed surfaces.
    """
    try:
        wire = Part.Wire(boundary_edges)
    except Exception:
        try:
            wire = Part.Compound(boundary_edges)
        except Exception:
            return None

    # Close the wire if needed
    try:
        if not wire.isClosed():
            vertices = wire.Vertexes
            if len(vertices) >= 2:
                start = vertices[0].Point
                end = vertices[-1].Point
                if (start - end).Length > 1e-6:
                    closing = Part.LineSegment(end, start).toShape()
                    wire = Part.Wire(list(wire.Edges) + [closing])
    except Exception:
        pass

    # Sweep the wire along the normal in BOTH directions by ribbon_half_width.
    # This creates a thin "band" surface centered on the boundary.
    n = FreeCAD.Vector(normal.x, normal.y, normal.z)
    if n.Length < 1e-9:
        return None
    n.normalize()

    try:
        ribbon_pos = wire.extrude(n * ribbon_half_width)
        ribbon_neg = wire.extrude(-n * ribbon_half_width)
        ribbon = ribbon_pos.fuse(ribbon_neg)
    except Exception as exc:
        print(f"[io] ribbon sweep failed: {exc}")
        return None

    # --- DEBUG PRINT ---
    try:
        print(f"[ribbon-debug] face bbox: {extended_face.BoundBox}")
        print(f"[ribbon-debug] ribbon bbox: {ribbon.BoundBox}")
    except Exception:
        pass
    # --- END DEBUG ---

    # Split the extended face using the ribbon
    try:
        import BOPTools.SplitAPI
        pieces = BOPTools.SplitAPI.slice(extended_face, [ribbon], "Standard", 0.0)
        pieces = list(pieces.Faces)
    except Exception as exc:
        print(f"[io] BOPTools split failed: {exc}")
        pieces = []

    if not pieces:
        # Fallback: manual split via Shape.split
        try:
            split_result = extended_face.split(ribbon)
            pieces = list(split_result.Faces)
        except Exception as exc:
            print(f"[io] Shape.split failed: {exc}")
            return None

    if not pieces:
        return None

    # --- DEBUG PRINTS ---
    print(f"[ribbon-debug] split produced {len(pieces)} piece(s)")
    for i, p in enumerate(pieces):
        try:
            print(f"[ribbon-debug]   piece {i}: area={p.Area:.2f}, bbox={p.BoundBox}")
        except Exception as e:
            print(f"[ribbon-debug]   piece {i}: cannot read area/bbox ({e})")
    # --- END DEBUG ---

    if len(pieces) == 1:
        # The ribbon didn't actually separate anything
        return pieces[0]

    # Compute the plane origin for the projection (center of the boundary)
    try:
        boundary_center = wire.CenterOfMass
    except Exception:
        boundary_center = extended_face.CenterOfMass

    scores = []
    for p in pieces:
        s = _piece_outside_score(p, boundary_edges, n, boundary_center)
        try:
            print(f"[score] piece area={p.Area:.2f}, score={s}")
        except Exception:
            pass
        if s is None:
            continue
        scores.append((s, p))

    if not scores:
        return extended_face

    # Sort by score descending — pieces with the highest "outside-ness" win.
    scores.sort(key=lambda t: t[0], reverse=True)

    # The best-scoring piece is the outer band. Include any other piece
    # that's ALSO outside (score > 0), in case the split produced multiple
    # outer bands.
    outer_pieces = [p for s, p in scores if s > 0]
    if not outer_pieces:
        # Fallback: keep the single best piece
        outer_pieces = [scores[0][1]]

    if len(outer_pieces) == 1:
        return outer_pieces[0]
    return Part.makeCompound(outer_pieces)


def _piece_is_inside_original_face(piece, original_face, tolerance=0.5):
    """Test whether a piece lies on the original face's side of the ribbon."""
    try:
        # Sample the piece's centroid
        c = piece.CenterOfMass
        # Project it onto the original face's surface
        u, v = original_face.Surface.parameter(c)
        u_min, u_max, v_min, v_max = original_face.ParameterRange
        # A small margin so pieces that touch the boundary count as inside
        margin_u = (u_max - u_min) * 0.05
        margin_v = (v_max - v_min) * 0.05
        inside_u = (u_min - margin_u) <= u <= (u_max + margin_u)
        inside_v = (v_min - margin_v) <= v <= (v_max + margin_v)
        return inside_u and inside_v
    except Exception:
        return None

def _piece_outside_score(piece, boundary_edges, plane_normal, boundary_center):
    """Return a score: positive = piece is outside the boundary,
    negative = inside. Magnitude is the projected distance from the
    polygon boundary in the plane.
    """
    try:
        # Build an orthonormal frame (u, v) perpendicular to plane_normal
        n = FreeCAD.Vector(plane_normal.x, plane_normal.y, plane_normal.z)
        if n.Length < 1e-9:
            return None
        n.normalize()

        # Pick a reference axis not parallel to n
        ref = FreeCAD.Vector(1, 0, 0)
        if abs(n.dot(ref)) > 0.9:
            ref = FreeCAD.Vector(0, 1, 0)

        u_axis = ref.cross(n)
        if u_axis.Length < 1e-9:
            return None
        u_axis.normalize()
        v_axis = n.cross(u_axis)
        v_axis.normalize()

        # Project the boundary polygon to 2D
        boundary_poly = []
        for edge in boundary_edges:
            for vertex in edge.Vertexes:
                p = vertex.Point - boundary_center
                boundary_poly.append((p.dot(u_axis), p.dot(v_axis)))

        # Remove duplicate consecutive points
        dedup = []
        for pt in boundary_poly:
            if not dedup or (abs(dedup[-1][0] - pt[0]) > 1e-6 or abs(dedup[-1][1] - pt[1]) > 1e-6):
                dedup.append(pt)
        if len(dedup) > 1 and abs(dedup[0][0] - dedup[-1][0]) < 1e-6 and abs(dedup[0][1] - dedup[-1][1]) < 1e-6:
            dedup = dedup[:-1]

        if len(dedup) < 3:
            return None

        # Project the piece centroid
        c = piece.CenterOfMass - boundary_center
        px, py = c.dot(u_axis), c.dot(v_axis)

        # Ray-cast to determine inside/outside
        inside = False
        j = len(dedup) - 1
        for i in range(len(dedup)):
            xi, yi = dedup[i]
            xj, yj = dedup[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i

        # Compute distance to nearest polygon edge
        min_dist = 1e18
        for i in range(len(dedup)):
            xi, yi = dedup[i]
            xj, yj = dedup[(i + 1) % len(dedup)]
            # Point-to-segment distance
            dx, dy = xj - xi, yj - yi
            seg_len_sq = dx*dx + dy*dy
            if seg_len_sq < 1e-12:
                d = ((px - xi)**2 + (py - yi)**2) ** 0.5
            else:
                t = max(0.0, min(1.0, ((px - xi) * dx + (py - yi) * dy) / seg_len_sq))
                proj_x = xi + t * dx
                proj_y = yi + t * dy
                d = ((px - proj_x)**2 + (py - proj_y)**2) ** 0.5
            if d < min_dist:
                min_dist = d

        # Signed score
        return min_dist if not inside else -min_dist

    except Exception as exc:
        print(f"[io] point-in-polygon failed: {exc}")
        return None


