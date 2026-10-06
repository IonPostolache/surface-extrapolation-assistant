"""Test topological outer-boundary detection."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore
from FreeCAD import Vector  # type: ignore


def test_boundary_faces_on_planar_sheet():
    """A single planar sheet: one face, all of it on the boundary."""
    from surface_assistant.topology import get_boundary_faces_no_curve

    sheet = Part.makePlane(20, 10)
    faces = get_boundary_faces_no_curve(sheet)
    assert len(faces) == 1


def test_boundary_faces_on_open_shell():
    """An open shell of two faces: both touch the outer boundary."""
    from surface_assistant.topology import get_boundary_faces_no_curve

    face_a = Part.makePlane(10, 10)
    face_b = Part.makePlane(10, 10, Vector(0, 10, 0), Vector(0, -1, 0))
    shell = Part.makeShell([face_a, face_b])

    faces = get_boundary_faces_no_curve(shell)
    assert len(faces) == 2


def test_boundary_faces_on_closed_solid_returns_empty():
    """A closed solid has no outer boundary in the surface sense.

    Every edge is shared by two faces, so no edge belongs to exactly
    one face. The function correctly returns an empty list.
    """
    from surface_assistant.topology import get_boundary_faces_no_curve

    box = Part.makeBox(10, 10, 10)
    faces = get_boundary_faces_no_curve(box)
    assert faces == []