"""Starter tests for topological utilities.

These tests are skipped if FreeCAD is not available on this machine,
so CI won't fail on machines without FreeCAD installed.
"""

import pytest

freecad = pytest.importorskip("FreeCAD")  # skip if FreeCAD not importable


def test_fingerprint_is_stable_for_same_face():
    from surface_assistant.topology import fingerprint_face
    import Part

    box = Part.makeBox(10, 10, 10)
    face = box.Faces[0]
    fp1 = fingerprint_face(face)
    fp2 = fingerprint_face(face)
    assert fp1 == fp2


def test_fingerprint_differs_for_different_faces():
    from surface_assistant.topology import fingerprint_face
    import Part

    box = Part.makeBox(10, 10, 10)
    fp_a = fingerprint_face(box.Faces[0])
    fp_b = fingerprint_face(box.Faces[1])
    assert fp_a != fp_b