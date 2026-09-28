"""Shared monotonic deadline for bounded I/O; no background execution."""

import time


class DeadlineExceeded(TimeoutError):
    def __init__(self, phase: str = "deadline_exhausted"):
        super().__init__("조사 시간 한도에 도달했습니다")
        self.phase = phase


def remaining_timeout(deadline: float | None, maximum: float) -> float:
    if deadline is None:
        return float(maximum)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded()
    return min(float(maximum), remaining)


def check_deadline(deadline: float | None) -> None:
    if deadline is not None:
        remaining_timeout(deadline, float("inf"))
