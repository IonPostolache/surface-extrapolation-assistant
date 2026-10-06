"""Test outer-loop selection from a surface with holes."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore
from FreeCAD import Vector  # type: ignore


def test_outer_perimeter_loop_ignores_hole():
    """A sheet with a small hole: outer loop wins, hole is ignored."""
    from surface_assistant.topology import get_outer_perimeter_loop

    # Outer square 20×20
    outer = Part.makePolygon([
        Vector(0, 0, 0), Vector(20, 0, 0),
        Vector(20, 20, 0), Vector(0, 20, 0), Vector(0, 0, 0),
    ])
    outer_face = Part.Face(outer)

    # Inner square 4×4 (hole)
    inner = Part.makePolygon([
        Vector(8, 8, 0), Vector(12, 8, 0),
        Vector(12, 12, 0), Vector(8, 12, 0), Vector(8, 8, 0),
    ])
    hole_face = Part.Face(inner)

    # A face with a hole in it
    holed_face = outer_face.cut(hole_face)

    # No need to wrap it in a shell — the function takes any shape
    loop = get_outer_perimeter_loop(holed_face)
    assert loop is not None
    # Should be the outer loop, not the hole
    assert loop.BoundBox.XLength > 15
    assert loop.BoundBox.YLength > 15


def test_outer_perimeter_loop_no_holes():
    """A sheet with no holes: the single loop is returned."""
    from surface_assistant.topology import get_outer_perimeter_loop

    sheet = Part.makePlane(20, 10)
    loop = get_outer_perimeter_loop(sheet)
    assert loop is not None
    assert loop.BoundBox.XLength > 15