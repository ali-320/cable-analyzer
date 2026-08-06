"""Shared helpers for the unit tests."""
import sys
from pathlib import Path

# make the src package importable no matter how unittest is invoked
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.telemetry.models import Sample  # noqa: E402


def make_samples(
    r_cable: float = 0.15,
    v_target: float = 5.0,
    current: float = 1.0,
    n: int = 250,
    period: float = 0.04,
    noise: float = 0.0,
    state: str = "CHARGING",
    r_growth_per_s: float = 0.0,  # Ohm/s linear drift (heating)
) -> list[Sample]:
    """Deterministic (V, I) stream where V = v_target - I*(R + drift*t)."""
    out = []
    for k in range(n):
        t = k * period
        r = r_cable + r_growth_per_s * t
        v = v_target - current * r
        out.append(Sample(t=t, voltage=v, current=current, power=v * current, state=state, valid=True))
    return out
