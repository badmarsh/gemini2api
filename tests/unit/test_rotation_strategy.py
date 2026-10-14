"""ROTATION_STRATEGY parsing must tolerate the underscore spelling.

The README used to document ``round_robin`` while the enum only knew
``round-robin``; setting the documented value raised ValueError at startup.
"""

import pytest

from app.core.account_pool import RotationStrategy


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("round-robin", RotationStrategy.ROUND_ROBIN),
        ("round_robin", RotationStrategy.ROUND_ROBIN),
        ("Round_Robin", RotationStrategy.ROUND_ROBIN),
        (" ROUND-ROBIN ", RotationStrategy.ROUND_ROBIN),
        ("failover", RotationStrategy.FAILOVER),
        ("FAILOVER", RotationStrategy.FAILOVER),
    ],
)
def test_rotation_strategy_accepts_variants(raw, expected):
    assert RotationStrategy(raw) is expected


@pytest.mark.parametrize("raw", ["", "random", "roundrobin", "least_used", None, 3])
def test_rotation_strategy_still_rejects_unknown_values(raw):
    with pytest.raises(ValueError):
        RotationStrategy(raw)
