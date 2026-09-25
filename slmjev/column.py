"""Column-level checks for structured data: what one cell alone cannot show.

A date of birth typed into a ``procedure_date`` column has the same shape as its neighbours
(``19830112`` among ``20230602``), so neither the judge, which sees one cell, nor a shape profiler
(structured_deidentification's ``se_profile_column``) can flag it. Its *value* can: it lies
decades before the rest of the column. :func:`date_outliers` finds such values; the judge then
never drops them (``cell_review``, reason ``column_outlier``).

Dates are read day-first (Singapore convention). Everything here is pure code.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from collections.abc import Sequence

from slmjev import rules

__all__ = ["date_outliers", "parse_date"]

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DMY = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})")
_YMD = re.compile(r"(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})")
_D_MON_Y = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[ \-]([A-Za-z]{3,9})[ \-,]*(\d{4})")
_MON_D_Y = re.compile(r"([A-Za-z]{3,9})[ \-](\d{1,2})(?:st|nd|rd|th)?[ \-,]*(\d{4})")


def _date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _month(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


def parse_date(value: str | None) -> dt.date | None:
    """A whole-value date in any common format, else None. Two-digit years pivot at 50."""
    s = (value or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{8}", s):
        return rules.parse_compact_date(s)
    if m := _YMD.fullmatch(s):
        return _date(int(m[1]), int(m[2]), int(m[3]))
    if m := _DMY.fullmatch(s):
        y = int(m[3])
        y += (1900 if y >= 50 else 2000) if len(m[3]) == 2 else 0
        return _date(y, int(m[2]), int(m[1]))
    if (m := _D_MON_Y.fullmatch(s)) and (mon := _month(m[2])):
        return _date(int(m[3]), mon, int(m[1]))
    if (m := _MON_D_Y.fullmatch(s)) and (mon := _month(m[1])):
        return _date(int(m[3]), mon, int(m[2]))
    return None


def date_outliers(values: Sequence[str | None], *, min_dates: int = 8, min_share: float = 0.7,
                  k: float = 3.0, min_gap_days: int = 730) -> list[bool]:
    """For each value of one column: is it a date far from the column's other dates?

    Only a date column qualifies: at least ``min_dates`` parseable dates making up at least
    ``min_share`` of the non-empty values; otherwise nothing is flagged. A date is an outlier when
    its distance from the column median exceeds ``k`` robust standard deviations (1.4826 x MAD),
    and never less than ``min_gap_days``. Flagging only sends a cell to review, so ``k`` leans
    low. A value within that band of the column (a recent DOB among recent dates) cannot be told
    apart by value alone."""
    parsed = [parse_date(v) for v in values]
    days = [d.toordinal() for d in parsed if d]
    nonempty = sum(1 for v in values if v and v.strip())
    if len(days) < min_dates or len(days) < min_share * nonempty:
        return [False] * len(values)
    med = statistics.median(days)
    mad = statistics.median(abs(x - med) for x in days)
    cut = max(k * 1.4826 * mad, min_gap_days)
    return [d is not None and abs(d.toordinal() - med) > cut for d in parsed]
