"""Generate a test STEP surface and boundary curve for the assistant.

Run headless:

    python examples/make_test_surface.py

Outputs (in examples/):
    test_surface.step
    test_boundary.step
"""

from __future__ import annotations

import sys
from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore
from FreeCAD import Vector  # type: ignore


OUT_DIR = Path(__file__).resolve().parent


def build_bent_plate():
    """Build a bent-plate solid and return its outer shell (two faces)."""
    import math

    base_length = 100.0
    base_depth = 60.0
    thickness = 1.0
    bend_radius = 30.0
    bend_angle_deg = 60.0
    angle_rad = math.radians(bend_angle_deg)

    # Build the profile in the YZ plane: an L-shape that bends at Y=base_depth.
    # Points going around the outer profile:
    #   (Y=0, Z=0) → (Y=base_depth, Z=0) → arc up to angle_rad
    #   → return path offset by thickness → close.
    # This is complex to do by hand.

    # Instead: use a swept profile. Build the flat plate, bend it via
    # Part::Thickness or a sweep. The simplest robust option:
    # build a wire from the base corners + arc, make a face, thicken it.

    pts = []
    # Base bottom edge along Y
    pts.append(Vector(0, 0, 0))
    pts.append(Vector(0, base_depth, 0))
    # Arc from (base_depth, 0) upward to angle_rad
    arc_center = Vector(0, base_depth, bend_radius)
    circle = Part.Circle(arc_center, Vector(1, 0, 0), bend_radius)
    arc = Part.ArcOfCircle(circle, -math.pi / 2, -math.pi / 2 + angle_rad)
    arc_pts = [arc.value(t) for t in [i/20 for i in range(21)]]
    pts.extend(arc_pts[1:])  # skip first, it duplicates (base_depth, 0)

    # Build the wire from these points
    edges = []
    for i in range(len(pts) - 1):
        edges.append(Part.LineSegment(pts[i], pts[i+1]).toShape())

    wire = Part.Wire(edges)
    face = Part.Face(wire)

    # Extrude along X to make a solid
    solid = face.extrude(Vector(base_length, 0, 0))

    # Now extract the two faces we care about: the flat bottom and the curved top
    # (in our parametrization, these are the two "long" faces of the solid).
    # Sorting by area to pick the two largest.
    faces_by_area = sorted(solid.Faces, key=lambda f: f.Area, reverse=True)
    shell = Part.makeShell(faces_by_area[:2])

    return shell


def build_boundary(shell: Part.Shape):
    """Return a compound containing the outer boundary edges of the shell."""
    # Use the shape's outer wire if present; otherwise fall back to all
    # boundary edges (edges shared by only one face).
    try:
        outer = shell.OuterWire
        return Part.Wire(outer)
    except Exception:
        # Fallback: collect edges that belong to exactly one face.
        from collections import Counter
        edge_keys = Counter()
        for f in shell.Faces:
            for e in f.Edges:
                # Loose key: midpoint + length rounded
                mp = e.CenterOfMass
                key = (round(mp.x, 3), round(mp.y, 3), round(mp.z, 3),
                       round(e.Length, 3))
                edge_keys[key] += 1
        boundary_edges = []
        for f in shell.Faces:
            for e in f.Edges:
                mp = e.CenterOfMass
                key = (round(mp.x, 3), round(mp.y, 3), round(mp.z, 3),
                       round(e.Length, 3))
                if edge_keys[key] == 1:
                    boundary_edges.append(e)
        return Part.Compound(boundary_edges)


def export(shape: Part.Shape, path: Path) -> None:
    doc = FreeCAD.newDocument("_ExportDoc")
    obj = doc.addObject("Part::Feature", "Surface")
    obj.Shape = shape
    doc.recompute()

    # Export as a single object
    Part.export([obj], str(path))
    FreeCAD.closeDocument(doc.Name)
    print(f"Wrote {path}")


def main() -> None:
    shell = build_bent_plate()
    print(f"Shell faces: {len(shell.Faces)}")
    print(f"Shell edges: {len(shell.Edges)}")

    boundary = build_boundary(shell)
    print(f"Boundary edges: {len(boundary.Edges)}")

    export(shell, OUT_DIR / "test_surface.step")
    export(boundary, OUT_DIR / "test_boundary.step")


if __name__ == "__main__":
    main()