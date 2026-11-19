from dataclasses import dataclass

from app.db import MonitorTask


@dataclass(frozen=True)
class CheckResult:
    code: str
    base_price: float
    old_base_price: float
    new_base_price: float | None
    current_price: float
    diff: float
    threshold: float
    direction: str
    grid_steps: int
    triggered: bool


class ThresholdChecker:
    def check(self, task: MonitorTask, current_price: float) -> CheckResult:
        diff = round(current_price - task.base_price, 2)
        threshold = abs(task.threshold)
        if threshold <= 0:
            raise ValueError(f"Monitor task {task.id} threshold must be greater than 0.")

        old_base_price = task.base_price
        direction = "FLAT"
        grid_steps = 0
        new_base_price = None

        if diff >= threshold:
            direction = "UP"
            grid_steps = int(diff // threshold)
            new_base_price = old_base_price + grid_steps * threshold
        elif diff <= -threshold:
            direction = "DOWN"
            grid_steps = int(abs(diff) // threshold)
            new_base_price = old_base_price - grid_steps * threshold

        return CheckResult(
            code=task.code,
            base_price=old_base_price,
            old_base_price=old_base_price,
            new_base_price=new_base_price,
            current_price=current_price,
            diff=diff,
            threshold=threshold,
            direction=direction,
            grid_steps=grid_steps,
            triggered=grid_steps > 0,
        )
