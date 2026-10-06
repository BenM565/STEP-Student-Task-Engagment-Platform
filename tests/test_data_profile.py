# Unit tests for deterministic dataset profiling and chart building.

import pytest

from ai_agents.data_profile import (
    BOOLEAN, CATEGORICAL, DATE, NUMERIC, TEXT, DatasetError, Table, _load_csv, build_chart, figures_supported,
    infer_columns, parse_number, profile_numbers, profile_table,
)


def table(columns, rows):
    return Table(name="t", columns=columns, rows=[[str(v) for v in r] for r in rows],
                 rows_truncated=False, columns_truncated=False)


@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", 1234.5), ("€99", 99), ("-$5", -5), ("12%", 12), ("1e3", 1000), (" 7 ", 7),
    ("1,5", None), ("abc", None), ("", None), ("12-03", None), ("nan", None),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_type_inference():
    rows = [[i, f"2025-01-{i:02d}", ["a", "b"][i % 2], "yes" if i % 3 else "no", f"note number {i}", f"{i}.5"]
            for i in range(1, 26)]
    cols = infer_columns(table(["id", "when", "group", "flag", "notes", "value"], rows))
    assert [c.kind for c in cols] == [NUMERIC, DATE, CATEGORICAL, BOOLEAN, TEXT, NUMERIC]
    assert cols[0].likely_identifier is True and cols[5].likely_identifier is False


def test_day_first_dates_detected():
    cols = infer_columns(table(["d"], [["13/01/2025"], ["14/02/2025"], ["01/03/2025"]]))
    assert cols[0].kind == DATE and cols[0].values[2].month == 3


def test_numeric_stats_and_outliers():
    values = [10, 12, 11, 13, 12, 11, 500]
    p = profile_table(table(["v"], [[v] for v in values]))
    stats = p["column_profiles"][0]["stats"]
    assert stats["sum"] == 569 and stats["median"] == 12 and stats["max"] == 500
    assert stats["outliers_iqr"]["count"] == 1
    assert stats["outliers_iqr"]["most_extreme"] == [{"value": 500, "row": 7}]


def test_missing_and_duplicates():
    p = profile_table(table(["a", "b"], [[1, "x"], [1, "x"], ["", "y"]]))
    assert p["duplicate_rows"] == 1
    assert p["column_profiles"][0]["missing"] == 1


def test_csv_semicolon_and_bom():
    t = _load_csv("﻿a;b\n1;2\n3;4\n".encode("utf-8"), "x.csv", 100, 100)
    assert t.columns == ["a", "b"] and t.rows == [["1", "2"], ["3", "4"]]


def test_csv_without_rows_rejected():
    with pytest.raises(DatasetError):
        _load_csv(b"a,b\n", "x.csv", 100, 100)


def test_row_limit_truncates():
    t = _load_csv(b"a\n" + b"\n".join(str(i).encode() for i in range(50)), "x.csv", 10, 100)
    assert len(t.rows) == 10 and t.rows_truncated


def test_line_chart_by_month():
    t = table(["d", "v"], [["2025-01-05", 10], ["2025-01-20", 5], ["2025-03-01", 7], ["2025-04-01", 1]])
    chart = build_chart(t, infer_columns(t), {"chart_type": "line", "x_column": "d", "y_column": "v", "aggregation": "sum"})
    assert chart["ok"] and chart["labels"] == ["2025-01", "2025-03", "2025-04"] and chart["values"] == [15, 7, 1]


def test_histogram_and_scatter():
    t = table(["a", "b"], [[i, i * 2] for i in range(30)])
    cols = infer_columns(t)
    hist = build_chart(t, cols, {"chart_type": "histogram", "x_column": "a", "y_column": None, "aggregation": "none"})
    assert hist["ok"] and sum(hist["values"]) == 30
    scatter = build_chart(t, cols, {"chart_type": "scatter", "x_column": "a", "y_column": "b", "aggregation": "none"})
    assert scatter["ok"] and scatter["points"][3] == {"x": 3, "y": 6}


def test_invalid_chart_suggestions_rejected():
    t = table(["cat", "txt"], [["a", "x"], ["b", "y"], ["a", "z"]])
    cols = infer_columns(t)
    assert not build_chart(t, cols, {"chart_type": "histogram", "x_column": "cat"})["ok"]
    assert "not numeric" in build_chart(t, cols, {"chart_type": "bar", "x_column": "cat", "y_column": "txt", "aggregation": "sum"})["error"]


def test_figures_supported():
    numbers = profile_numbers({"a": 1234.5, "b": [{"pct": 12.3}], "sample_rows": {"rows": [[999]]}})
    assert figures_supported("Revenue was 1,234.50 and 12.3% of rows in 2025", numbers) == (True, [])
    ok, unmatched = figures_supported("It grew 45% and 999 orders", numbers)
    assert not ok and unmatched == ["45", "999"]  # sample rows are not evidence


def test_histogram_excludes_extreme_outliers_and_says_so():
    t = table(["v"], [[v] for v in list(range(1, 40)) + [100000]])
    chart = build_chart(t, infer_columns(t), {"chart_type": "histogram", "x_column": "v", "aggregation": "none"})
    assert chart["ok"] and sum(chart["values"]) == 39
    assert chart["note"] == "1 outlier outside 1–39 not shown"
