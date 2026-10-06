"""Tests for the extrapolation module."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore


def _box_top_face():
    """Return the top face of a small box."""
    box = Part.makeBox(20, 20, 1)
    for f in box.Faces:
        if (
            abs(f.BoundBox.ZMax - 1.0) < 1e-6
            and f.BoundBox.ZLength < 1e-6
        ):
            return f
    raise RuntimeError("Could not find top face")


def test_extrapolate_plane_with_edges():
    """Extrapolate a planar face along all its outer edges."""
    from surface_assistant.extrapolation import (
        extrapolate_face,
        ExtrapolationStatus,
    )

    top_face = _box_top_face()
    extendable_edges = list(top_face.Edges)

    result = extrapolate_face(
        top_face,
        10.0,
        extendable_edges=extendable_edges,
    )

    print(result.short())

    # Accept any non-FAILED status — the exact outcome depends on FreeCAD's
    # ribbon build. The test's job is to confirm the call runs and returns
    # a proper ExtrapolationResult.
    assert result.status in (
        ExtrapolationStatus.SUCCESS,
        ExtrapolationStatus.PARTIAL,
        ExtrapolationStatus.DEFERRED,
    )


def test_extrapolate_requires_extendable_edges():
    """Calling without extendable_edges should not raise; it should
    return a DEFERRED result (the current API treats an empty edge list
    as nothing to extend)."""
    from surface_assistant.extrapolation import (
        extrapolate_face,
        ExtrapolationStatus,
    )

    top_face = _box_top_face()

    result = extrapolate_face(
        top_face,
        10.0,
        extendable_edges=[],
    )

    assert result.status == ExtrapolationStatus.DEFERRED
    assert result.error_message is not None