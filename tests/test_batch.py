"""Test that the batch loop continues when a face fails."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")
import Part  # type: ignore


def test_batch_report_aggregation():
    """Verify the BatchReport correctly classifies results."""
    from surface_assistant.batch import BatchReport
    from surface_assistant.extrapolation import (
        ExtrapolationResult,
        ExtrapolationStatus,
    )

    report = BatchReport(
        step_file="x.step",
        boundary_file="b.step",
        target_mm=100.0,
        tolerance_percent=2.0,
        total_faces=3,
    )
    report.results = [
        ExtrapolationResult(
            status=ExtrapolationStatus.SUCCESS,
            face_index=0,
            requested_mm=100.0,
            achieved_mm=99.5,
        ),
        ExtrapolationResult(
            status=ExtrapolationStatus.PARTIAL,
            face_index=1,
            requested_mm=100.0,
            achieved_mm=92.0,
        ),
        ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=2,
            requested_mm=100.0,
            error_message="test failure",
        ),
    ]

    assert len(report.successes) == 1
    assert len(report.partials) == 1
    assert len(report.failures) == 1
    assert report.success_rate == pytest.approx(33.3, abs=0.1)
    assert "test failure" in report.summary()