import datetime as dt

import pytest

from slmjev import column


@pytest.mark.parametrize(("s", "want"), [
    ("20230602", dt.date(2023, 6, 2)),
    ("2023-06-02", dt.date(2023, 6, 2)),
    ("02/06/2023", dt.date(2023, 6, 2)),  # day first
    ("02.06.23", dt.date(2023, 6, 2)),
    ("12/01/83", dt.date(1983, 1, 12)),  # two-digit years pivot at 50
    ("2 Jun 2023", dt.date(2023, 6, 2)),
    ("2nd June, 2023", dt.date(2023, 6, 2)),
    ("June 2, 2023", dt.date(2023, 6, 2)),
    (" 2 Jun 2023 ", dt.date(2023, 6, 2)),
])
def test_parse_date_reads_common_formats(s, want):
    assert column.parse_date(s) == want


@pytest.mark.parametrize("s", ["", None, "31/02/2023", "S1234567D", "20231345", "3/7", "Jun 2023",
                               "2 Foo 2023", "on 2 Jun 2023"])
def test_parse_date_rejects_non_dates(s):
    assert column.parse_date(s) is None


def _dates(n, start=dt.date(2023, 1, 1), step=17):
    return [(start + dt.timedelta(days=i * step)).strftime("%Y%m%d") for i in range(n)]


def test_a_dob_in_a_procedure_date_column_stands_out():
    col = [*_dates(20), "19830112", "", *_dates(5, dt.date(2025, 3, 1))]
    flags = column.date_outliers(col)
    assert flags[20] and sum(flags) == 1


def test_mixed_formats_and_close_dates_are_not_flagged():
    col = [*_dates(10), "2 Jun 2022", "2024-06-02", "15/08/2024"]
    assert not any(column.date_outliers(col))
    assert column.date_outliers([*col, "15/08/2027"])[-1]  # over two years out
    # a DOB within the gap of the column's dates cannot be told apart by value
    assert not any(column.date_outliers([*_dates(10), "20220101"]))


def test_only_date_columns_qualify():
    assert not any(column.date_outliers(["19830112", *_dates(5)]))  # too few dates
    mostly_ids = [*_dates(8), *[f"S{i:07d}A" for i in range(10)], "19830112"]
    assert not any(column.date_outliers(mostly_ids))  # dates are not the column's type
    assert column.date_outliers([]) == []
