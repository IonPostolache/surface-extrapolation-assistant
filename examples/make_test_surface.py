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
    """Build a flat base with a curved flange in one piece.

    The base is a rectangle in the XY plane. The flange starts at the
    Y = 0 edge, bends upward around a radius-30 axis along X.
    """
    base_length = 100.0      # along X
    base_depth = 60.0        # along Y
    bend_radius = 30.0
    bend_angle_deg = 60.0

    # 1. Base rectangle at Z = 0
    base = Part.makePlane(
        base_length,
        base_depth,
        Vector(0, 0, 0),
        Vector(0, 0, 1),
    )

    # 2. Curved flange: a cylinder sector tangent to the base at Y = base_depth
    import math
    angle_rad = math.radians(bend_angle_deg)
    arc_length = bend_radius * angle_rad

    # Sweep along the X axis: take a 2D profile in the YZ plane and extrude
    # it along X. The profile is an arc of radius `bend_radius`, starting
    # tangent to the base at (Y=base_depth, Z=0).
    #
    # Parameter t in [0, angle_rad]:
    #   y(t) = base_depth + bend_radius * sin(t)
    #   z(t) = bend_radius - bend_radius * cos(t)

    import Part as P

    def arc_point(t: float) -> Vector:
        return Vector(
            0.0,
            base_depth + bend_radius * math.sin(t),
            bend_radius - bend_radius * math.cos(t),
        )

    # Build the arc as a portion of a circle
    # Center is at (0, base_depth, bend_radius) because the arc is tangent
    # to the base at Y=base_depth, Z=0.
    arc_center = Vector(0.0, base_depth, bend_radius)
    arc_axis = Vector(1, 0, 0)   # normal along X, so the arc sweeps in the YZ plane

    circle = Part.Circle(arc_center, arc_axis, bend_radius)

    # Angles measured from the local X-axis of the circle's plane.
    # Since the axis is X, the local plane is YZ. We want the arc to start
    # at the point (Y=base_depth, Z=0) and sweep upward.
    # Starting angle = -pi/2 (pointing toward -Z from center, i.e., toward base)
    # Ending angle   = -pi/2 + angle_rad
    arc = Part.ArcOfCircle(circle, -math.pi / 2, -math.pi / 2 + angle_rad)
    arc_edge = arc.toShape()


    # Build the arc as an edge
    # arc_edge = P.Edge.makeCircle(
    #     radius=bend_radius,
    #     center=Vector(0, base_depth, bend_radius),
    #     normal=Vector(1, 0, 0),
    #     angle1=-math.pi / 2,
    #     angle2=-math.pi / 2 + angle_rad,
    # )

    # Extrude the arc along X to make the flange face
    flange = arc_edge.extrude(Vector(base_length, 0, 0))
    flange_face = flange.Faces[0]

    # 3. Combine into a shell (two faces joined at the common edge)
    # shell = Part.makeShell([base.Face, flange_face])
    shell = Part.makeShell([base, flange_face])

    # Try to sew the shell into a unified shape
    try:
        sewn = shell.sewShape()
        sewn = sewn.removeSplitter()
        if len(sewn.Faces) >= 1:
            shell = sewn
    except Exception:
        pass  # Fall back to the un-sewn shell

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



# def export(shape: Part.Shape, path: Path) -> None:
#     # Wrap in a document object first, then export via the doc — this
#     # preserves the shell topology better than Part.export on a raw Shape.
#     doc = FreeCAD.newDocument("_ExportDoc")
#     obj = doc.addObject("Part::Feature", "Surface")
#     obj.Shape = shape
#     doc.recompute()
#     Part.export([obj], str(path))
#     FreeCAD.closeDocument(doc.Name)
#     print(f"Wrote {path}")

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