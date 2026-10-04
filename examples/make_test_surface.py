"""Generate a test STEP surface for the assistant.

Run headless:

    python examples/make_test_surface.py

Outputs (in examples/):
    test_surface.step
"""

from __future__ import annotations

import math
from pathlib import Path

from surface_assistant import freecad_setup  # noqa: F401

import FreeCAD  # type: ignore
import Part     # type: ignore
from FreeCAD import Vector  # type: ignore


OUT_DIR = Path(__file__).resolve().parent


def build_bent_plate_surface():
    """Build a bent-plate surface: a flat base with a curved flange.

    The result is a single connected shell of two faces (planar base
    and cylindrical flange) that share an edge. No holes, no separate
    boundary curve — the pipeline detects the outer boundary
    topologically.
    """
    base_length = 100.0
    base_depth = 60.0
    bend_radius = 30.0
    bend_angle_deg = 60.0
    angle_rad = math.radians(bend_angle_deg)

    # --- Base face (planar, in the XY plane) ---
    p0 = Vector(0.0, 0.0, 0.0)
    p1 = Vector(base_length, 0.0, 0.0)
    p2 = Vector(base_length, base_depth, 0.0)
    p3 = Vector(0.0, base_depth, 0.0)

    base_wire = Part.Wire([
        Part.LineSegment(p0, p1).toShape(),
        Part.LineSegment(p1, p2).toShape(),
        Part.LineSegment(p2, p3).toShape(),
        Part.LineSegment(p3, p0).toShape(),
    ])
    base_face = Part.Face(base_wire)

    # --- Flange face (cylindrical, starting at Y = base_depth, curving up) ---
    # The arc's center is at (0, base_depth, bend_radius), the axis is along X.
    arc_center = Vector(0.0, base_depth, bend_radius)
    arc_axis = Vector(1, 0, 0)
    circle = Part.Circle(arc_center, arc_axis, bend_radius)
    arc = Part.ArcOfCircle(
        circle,
        -math.pi / 2,
        -math.pi / 2 + angle_rad,
    )
    arc_edge = arc.toShape()

    # Extrude the arc along X to make the flange surface
    flange = arc_edge.extrude(Vector(base_length, 0, 0))
    flange_face = flange.Faces[0]

    # --- Sew into a single connected shell ---
    shell = Part.makeShell([base_face, flange_face])

    # Try to sew / refine the shell so the two faces share an edge
    try:
        sewn = shell.sewShape(1e-4)
        shell = sewn
    except Exception:
        pass

    return shell


def export(shape: Part.Shape, path: Path) -> None:
    doc = FreeCAD.newDocument("_ExportDoc")
    obj = doc.addObject("Part::Feature", "Surface")
    obj.Shape = shape
    doc.recompute()

    Part.export([obj], str(path))
    FreeCAD.closeDocument(doc.Name)
    print(f"Wrote {path}")


def main() -> None:
    shell = build_bent_plate_surface()
    print(f"Shell type:  {type(shell).__name__}")
    print(f"Shell faces: {len(shell.Faces)}")
    print(f"Shell edges: {len(shell.Edges)}")

    export(shell, OUT_DIR / "test_surface.step")


if __name__ == "__main__":
    main()