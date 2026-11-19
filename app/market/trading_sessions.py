"""Domestic futures night-session recognition used by the monitoring scheduler.

The table intentionally contains only products whose session end is maintained
here with confidence.  Unknown products are surfaced to the dashboard and use
the conservative 02:30 fallback instead of being silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time


NIGHT_END_2300 = time(23, 0)
NIGHT_END_0100 = time(1, 0)
NIGHT_END_0230 = time(2, 30)
FALLBACK_NIGHT_END = NIGHT_END_0230
NIGHT_SESSION_REFERENCE_START = time(20, 50)

# Keyed by (exchange suffix, product). Keep additions here rather than in the
# worker so the supported schedule scope remains explicit and testable.
NIGHT_SESSION_ENDS: dict[tuple[str, str], time] = {
    ("SHF", "CU"): NIGHT_END_0100,
    ("SHF", "AL"): NIGHT_END_0100,
    ("SHF", "ZN"): NIGHT_END_0100,
    ("SHF", "PB"): NIGHT_END_0100,
    ("SHF", "NI"): NIGHT_END_0100,
    ("SHF", "SN"): NIGHT_END_0100,
    ("SHF", "SP"): NIGHT_END_2300,
    ("SHF", "AU"): NIGHT_END_0230,
    ("SHF", "AG"): NIGHT_END_0230,
    ("CZC", "SR"): NIGHT_END_2300,
    ("INE", "NR"): NIGHT_END_2300,
    ("INE", "SC"): NIGHT_END_0230,
}

# Explicitly maintained day-only products.  Anything not listed here or above
# is intentionally treated as unknown rather than guessed.
NO_NIGHT_SESSION_PRODUCTS = {
    ("GFE", "LC"),
    ("CFE", "IF"),
    ("CFE", "IH"),
    ("CFE", "IC"),
    ("CFE", "IM"),
}

_CONTRACT_CODE_RE = re.compile(r"^(?P<product>[A-Z]+)\d+[._-]?(?P<exchange>[A-Z]{2,6})$")


@dataclass(frozen=True)
class ContractSession:
    code: str
    product: str | None
    exchange: str | None
    night_end: time | None
    status: str
    message: str

    @property
    def has_night_session(self) -> bool:
        return self.night_end is not None


def recognize_contract_session(
    contract_code: str,
    overrides: dict[tuple[str, str], time | None] | None = None,
) -> ContractSession:
    code = (contract_code or "").strip().upper()
    match = _CONTRACT_CODE_RE.fullmatch(code)
    if not match:
        return ContractSession(
            code=code,
            product=None,
            exchange=None,
            night_end=FALLBACK_NIGHT_END,
            status="unknown",
            message="交易时段未识别，夜盘按 02:30 兜底。",
        )

    product = match.group("product")
    exchange = match.group("exchange")
    key = (exchange, product)
    if overrides and key in overrides:
        night_end = overrides[key]
        if night_end is None:
            return ContractSession(
                code=code,
                product=product,
                exchange=exchange,
                night_end=None,
                status="override_no_night",
                message="手动覆盖：无夜盘。",
            )
        return ContractSession(
            code=code,
            product=product,
            exchange=exchange,
            night_end=night_end,
            status="override",
            message=f"手动覆盖：夜盘至 {format_clock(night_end)}。",
        )
    if key in NIGHT_SESSION_ENDS:
        night_end = NIGHT_SESSION_ENDS[key]
        return ContractSession(
            code=code,
            product=product,
            exchange=exchange,
            night_end=night_end,
            status="night",
            message=f"夜盘至 {format_clock(night_end)}。",
        )
    if key in NO_NIGHT_SESSION_PRODUCTS:
        return ContractSession(
            code=code,
            product=product,
            exchange=exchange,
            night_end=None,
            status="no_night",
            message="无夜盘。",
        )
    return ContractSession(
        code=code,
        product=product,
        exchange=exchange,
        night_end=FALLBACK_NIGHT_END,
        status="unknown",
        message="交易时段未识别，夜盘按 02:30 兜底，请补充该品种时段。",
    )


def format_clock(value: time | None) -> str:
    if value is None:
        return "--:--"
    return value.strftime("%H:%M")


def minutes_after_session_start(value: time, start: time = NIGHT_SESSION_REFERENCE_START) -> int:
    """Return elapsed minutes from the night start, correctly crossing midnight."""
    start_minutes = start.hour * 60 + start.minute
    value_minutes = value.hour * 60 + value.minute
    if value_minutes <= start_minutes:
        value_minutes += 24 * 60
    return value_minutes - start_minutes
