"""Tests for the BatchReport aggregation logic."""

from surface_assistant import freecad_setup  # noqa: F401

import pytest

freecad = pytest.importorskip("FreeCAD")


def test_batch_report_aggregation():
    """Verify the BatchReport correctly classifies results."""
    from surface_assistant.batch import BatchReport
    from surface_assistant.extrapolation import (
        ExtrapolationResult,
        ExtrapolationStatus,
    )

    report = BatchReport(
        step_file="x.stp",
        target_mm=100.0,
        tolerance_percent=2.0,
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
            status=ExtrapolationStatus.DEFERRED,
            face_index=2,
            requested_mm=100.0,
            achieved_mm=100.0,
        ),
        ExtrapolationResult(
            status=ExtrapolationStatus.FAILED,
            face_index=3,
            requested_mm=100.0,
            error_message="test failure",
        ),
    ]

    # Classification
    assert len(report.successes) == 1
    assert len(report.partials) == 1
    assert len(report.failures) == 1
    # DEFERRED is its own category
    assert report.success_rate == 25.0  # 1 of 4
    # Summary is a string and contains the failure message
    assert "test failure" in report.summary()


def test_batch_report_success_rate_empty():
    """Empty results should produce a success rate of 0.0, not crash."""
    from surface_assistant.batch import BatchReport

    report = BatchReport(step_file="x.stp", target_mm=10.0, tolerance_percent=2.0)
    assert report.success_rate == 0.0


def test_batch_report_success_rate_all_success():
    from surface_assistant.batch import BatchReport
    from surface_assistant.extrapolation import (
        ExtrapolationResult,
        ExtrapolationStatus,
    )

    report = BatchReport(step_file="x.stp", target_mm=10.0, tolerance_percent=2.0)
    report.results = [
        ExtrapolationResult(
            status=ExtrapolationStatus.SUCCESS,
            face_index=i,
            requested_mm=10.0,
            achieved_mm=10.0,
        )
        for i in range(4)
    ]
    assert report.success_rate == 100.0