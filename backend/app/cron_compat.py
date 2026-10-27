from __future__ import annotations

from datetime import datetime, timedelta

try:
    from croniter import croniter as _croniter
except ImportError:  # pragma: no cover - dependency is installed from pyproject in normal use
    _croniter = None


def _fallback_next(spec: str, base: datetime) -> datetime:
    fields = spec.split()
    if len(fields) != 5:
        raise ValueError("Cron expression must have five fields")
    minute, hour, day, month, weekday = fields
    candidate = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        checks = ((minute, candidate.minute), (hour, candidate.hour), (day, candidate.day), (month, candidate.month), (weekday, (candidate.weekday() + 1) % 7))
        if all(value == "*" or str(number) in value.split(",") for value, number in checks):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("Cron expression has no next occurrence")


def next_cron(spec: str, base: datetime) -> datetime:
    if _croniter is not None:
        return _croniter(spec, base).get_next(datetime)
    return _fallback_next(spec, base)
