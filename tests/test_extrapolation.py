"""Tests for calibrated extrapolation."""

from surface_assistant import freecad_setup  # noqa: F401  — must run before FreeCAD import

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore


def test_extrapolate_plane_all_directions():
    from surface_assistant.extrapolation import (
        extrapolate_face,
        ExtrapolationStatus,
    )

    box = Part.makeBox(20, 20, 1)
    top_face = None
    for f in box.Faces:
        if abs(f.BoundBox.ZMax - 1.0) < 1e-6 and f.BoundBox.ZLength < 1e-6:
            top_face = f
            break
    assert top_face is not None

    result = extrapolate_face(top_face, 10.0, direction="all")
    print(result.short())

    assert result.status in (
        ExtrapolationStatus.SUCCESS,
        ExtrapolationStatus.PARTIAL,
    )
    assert result.achieved_mm is not None
    assert result.achieved_mm > 0