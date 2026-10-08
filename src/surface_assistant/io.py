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

    Writes:
        - One Extended_i per extended face (or one for the whole
          ribbon compound, depending on strategy).
        - One JoinedShell containing extension + original + interior.
        - One OriginalSurface with the original input geometry, for
          colour-comparison workflows.
    """
    output_path = Path(output_path)
    doc = FreeCAD.newDocument("_SavedOutput")

    for i, face in enumerate(report.extended_faces):
        obj = doc.addObject("Part::Feature", f"Extended_{i}")
        obj.Shape = face

    if report.join_result and report.join_result.sewed_shell is not None:
        joined = doc.addObject("Part::Feature", "JoinedShell")
        joined.Shape = report.join_result.sewed_shell

    # NEW — separate original surface for colour comparison
    if getattr(report, "original_shape", None) is not None:
        original = doc.addObject("Part::Feature", "OriginalSurface")
        original.Shape = report.original_shape

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

    snapshot_script = Path(__file__).resolve().parents[2] / "snapshot_views.py"
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

def make_screenshot_grid(
    fcstd_path: Path,
    output_png: Path,
    views: tuple[str, ...] = ("iso", "front", "top", "left", "rear"),
    cell_size: int = 512,
) -> Path | None:
    """Render multiple views and stitch them into a single grid PNG.

    Layout:
        - 6 views  → 3 columns × 2 rows
        - 4 views  → 2 × 2
        - 2 views  → 2 × 1
        - 1 view   → single image

    Each cell is labeled with the view name in the corner.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("[io] PIL (Pillow) not available; cannot build grid")
        return None

    # Render each view to a temporary PNG
    temp_png = output_png.with_suffix(".tmp.png")
    from surface_assistant.io import render_fcstd_to_png_subprocess
    if not render_fcstd_to_png_subprocess(
        fcstd_path, temp_png, size=cell_size, views=views
    ):
        return None

    # Collect the rendered files
    if len(views) == 1:
        rendered = [temp_png]
    else:
        rendered = [
            temp_png.with_name(f"{temp_png.stem}_{v}{temp_png.suffix}")
            for v in views
        ]

    images = []
    for v, path in zip(views, rendered):
        if not path.exists():
            continue
        try:
            img = Image.open(path).convert("RGB")
            images.append((v, img))
        except Exception as exc:
            print(f"[io] failed to open {path}: {exc}")

    if not images:
        print("[io] no images to stitch")
        return None

    # Decide grid layout
    n = len(images)
    if n == 1:
        cols, rows = 1, 1
    elif n == 2:
        cols, rows = 2, 1
    elif n <= 4:
        cols, rows = 2, 2
    else:
        cols, rows = 3, 2

    # Compute canvas size
    cell_w = max(img.width for _, img in images)
    cell_h = max(img.height for _, img in images)
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(canvas)

    for i, (view_name, img) in enumerate(images):
        r, c = divmod(i, cols)
        x = c * cell_w
        y = r * cell_h
        canvas.paste(img, (x, y))
        # Label the cell
        draw.text((x + 10, y + 10), view_name.upper(), fill="black")

    canvas.save(output_png, "PNG")

    # Clean up the temporary per-view files
    for path in rendered:
        try:
            path.unlink()
        except Exception:
            pass

    return output_png