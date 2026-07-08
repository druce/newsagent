import math

from lib.config import RATING_COEFFS


def test_coverage_coefficient_present_and_unit_scale():
    assert "coverage" in RATING_COEFFS
    assert RATING_COEFFS["coverage"] == 1.0


def _coverage_boost(coverage_count: int) -> float:
    # Mirror the exact expression added in rate.py.
    c = RATING_COEFFS
    return c["coverage"] * math.log2(max(int(coverage_count), 1))


def test_singleton_gets_no_boost():
    assert _coverage_boost(1) == 0.0


def test_boost_is_log2_of_group_size():
    assert _coverage_boost(2) == 1.0
    assert _coverage_boost(4) == 2.0
    assert _coverage_boost(8) == 3.0


def test_missing_or_zero_count_defaults_to_no_boost():
    assert _coverage_boost(0) == 0.0  # clamped to 1 → log2(1)=0
