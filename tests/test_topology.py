"""Tests for topological utilities."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore


def test_fingerprint_is_stable_for_same_face():
    from surface_assistant.topology import fingerprint_face

    box = Part.makeBox(10, 10, 10)
    face = box.Faces[0]
    fp1 = fingerprint_face(face)
    fp2 = fingerprint_face(face)
    assert fp1 == fp2


def test_fingerprint_differs_for_different_faces():
    from surface_assistant.topology import fingerprint_face

    box = Part.makeBox(10, 10, 10)
    fp_a = fingerprint_face(box.Faces[0])
    fp_b = fingerprint_face(box.Faces[1])
    assert fp_a != fp_b