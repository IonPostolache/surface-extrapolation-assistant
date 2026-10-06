"""Test the point-in-polygon classification used by the ribbon trim."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import FreeCAD  # type: ignore
import Part  # type: ignore


def test_piece_outside_score_sign():
    """A piece inside the boundary scores negative; outside scores positive."""
    from surface_assistant.io import _piece_outside_score

    square = Part.makePolygon([
        FreeCAD.Vector(0, 0, 0),
        FreeCAD.Vector(10, 0, 0),
        FreeCAD.Vector(10, 10, 0),
        FreeCAD.Vector(0, 10, 0),
        FreeCAD.Vector(0, 0, 0),
    ])
    boundary_edges = list(square.Edges)
    normal = FreeCAD.Vector(0, 0, 1)
    center = FreeCAD.Vector(5, 5, 0)

    inside = Part.makeBox(2, 2, 0.01, FreeCAD.Vector(4, 4, 0)).Faces[0]
    s_in = _piece_outside_score(inside, boundary_edges, normal, center)
    assert s_in is not None and s_in < 0

    outside = Part.makeBox(2, 2, 0.01, FreeCAD.Vector(15, 15, 0)).Faces[0]
    s_out = _piece_outside_score(outside, boundary_edges, normal, center)
    assert s_out is not None and s_out > 0