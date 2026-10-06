# Deterministic dataset profiling for the Data Analysis agent.
# STEP computes every statistic here in Python; the model only interprets the
# profile. Language models are unreliable at arithmetic over raw rows, so the
# raw data never goes to the model beyond a small sample.

import csv
import io
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

NUMERIC, DATE, BOOLEAN, CATEGORICAL, TEXT, EMPTY = "numeric", "date", "boolean", "categorical", "text", "empty"

_TYPE_THRESHOLD = 0.95
_BOOL_VALUES = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}
_NUMBER_RE = re.compile(r"^[-+]?(\d+(\.\d+)?|\.\d+)([eE][-+]?\d+)?$")
_THOUSANDS_RE = re.compile(r"^[-+]?\d{1,3}(,\d{3})+(\.\d+)?$")
_DATE_FORMATS_DAY_FIRST = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d",
                           "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d/%m/%Y %H:%M")
_DATE_FORMATS_MONTH_FIRST = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d",
                             "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m/%d/%Y %H:%M")

MAX_CATEGORIES = 10
MAX_PERIODS = 36
MAX_CHART_POINTS = 500
MAX_CHART_CATEGORIES = 15


class DatasetError(ValueError):
    pass


@dataclass
class Table:
    name: str
    columns: List[str]
    rows: List[List[str]]
    rows_truncated: bool
    columns_truncated: bool


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_table(path: str, ext: str, name: str, max_rows: int, max_cols: int) -> Table:
    if ext == ".csv":
        with open(path, "rb") as fh:
            raw = fh.read()
        return _load_csv(raw, name, max_rows, max_cols)
    if ext == ".xlsx":
        return _load_xlsx(path, name, max_rows, max_cols)
    raise DatasetError(f"'{name}' is not a CSV or XLSX file.")


def _load_csv(raw: bytes, name: str, max_rows: int, max_cols: int) -> Table:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DatasetError(f"'{name}' is not in a readable text encoding (use UTF-8).")
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return _build_table(reader, name, max_rows, max_cols)


def _load_xlsx(path: str, name: str, max_rows: int, max_cols: int) -> Table:
    try:
        import openpyxl
    except ImportError as exc:
        raise DatasetError("Excel support is not installed on this server (openpyxl).") from exc
    try:
        # data_only: use the values Excel last calculated, never evaluate formulas
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise DatasetError(f"'{name}' could not be opened as an Excel workbook.") from exc
    try:
        sheet = workbook.worksheets[0]
        rows = ([_cell_to_str(v) for v in row] for row in sheet.iter_rows(values_only=True))
        table = _build_table(rows, f"{name} (sheet '{sheet.title}')", max_rows, max_cols)
    finally:
        workbook.close()
    return table


def _cell_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") if (value.hour or value.minute or value.second) else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _build_table(row_iter, name: str, max_rows: int, max_cols: int) -> Table:
    header = None
    rows = []
    rows_truncated = False
    for row in row_iter:
        if header is None:
            if not any(str(c).strip() for c in row):
                continue  # skip leading blank rows
            header = [str(c).strip() for c in row]
            continue
        if not any(str(c).strip() for c in row):
            continue
        if len(rows) >= max_rows:
            rows_truncated = True
            break
        rows.append([str(c).strip() for c in row])
    if header is None:
        raise DatasetError(f"'{name}' has no header row.")

    width = max([len(header)] + [len(r) for r in rows[:1000]])
    columns_truncated = width > max_cols
    width = min(width, max_cols)
    columns = []
    seen = Counter()
    for i in range(width):
        label = header[i] if i < len(header) and header[i] else f"Column {i + 1}"
        seen[label] += 1
        columns.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    rows = [(r + [""] * width)[:width] for r in rows]
    if not rows:
        raise DatasetError(f"'{name}' has a header row but no data rows.")
    return Table(name=name, columns=columns, rows=rows, rows_truncated=rows_truncated, columns_truncated=columns_truncated)


# ---------------------------------------------------------------------------
# Parsing and type inference
# ---------------------------------------------------------------------------

def parse_number(value: str) -> Optional[float]:
    v = value.strip().replace(" ", "").replace(" ", "")
    if not v:
        return None
    v = v.lstrip("€$£").rstrip("%")
    if v.startswith(("-€", "-$", "-£")):
        v = "-" + v[2:]
    if _THOUSANDS_RE.match(v):
        v = v.replace(",", "")
    if not _NUMBER_RE.match(v):
        return None
    try:
        number = float(v)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _parse_date(value: str, formats) -> Optional[datetime]:
    v = value.strip()
    if not v or len(v) > 25:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


@dataclass
class ColumnInfo:
    name: str
    index: int
    kind: str
    values: list  # parsed non-empty values (float, datetime, bool or str)
    row_numbers: List[int]  # 1-based data row number for each parsed value
    missing: int
    note: Optional[str] = None
    likely_identifier: bool = False


def infer_columns(table: Table) -> List[ColumnInfo]:
    return [_infer(table, i) for i in range(len(table.columns))]


def _infer(table: Table, index: int) -> ColumnInfo:
    name = table.columns[index]
    raw = [(n, row[index]) for n, row in enumerate(table.rows, start=1)]
    present = [(n, v) for n, v in raw if v.strip()]
    missing = len(raw) - len(present)
    if not present:
        return ColumnInfo(name, index, EMPTY, [], [], missing)

    lowered = {v.strip().lower() for _, v in present}
    if lowered <= _BOOL_VALUES and len(lowered) <= 2 and not lowered <= {"0", "1"}:
        truthy = {"true", "yes", "y", "1", "t"}
        return ColumnInfo(name, index, BOOLEAN, [v.strip().lower() in truthy for _, v in present],
                          [n for n, _ in present], missing)

    numbers = [(n, parse_number(v)) for n, v in present]
    numeric_ok = [(n, x) for n, x in numbers if x is not None]
    if len(numeric_ok) / len(present) >= _TYPE_THRESHOLD:
        values = [x for _, x in numeric_ok]
        info = ColumnInfo(name, index, NUMERIC, values, [n for n, _ in numeric_ok], missing + len(present) - len(numeric_ok))
        info.likely_identifier = (
            len(values) > 20 and all(v.is_integer() for v in values) and len(set(values)) == len(values)
            and (values == sorted(values)) and bool(re.search(r"(^|_|\s)(id|no|number|ref)$", name.strip().lower()))
        )
        if len(numeric_ok) < len(present):
            info.note = f"{len(present) - len(numeric_ok)} non-numeric value(s) ignored"
        return info

    dates, note = _infer_dates(present)
    if dates is not None:
        return ColumnInfo(name, index, DATE, [d for _, d in dates], [n for n, _ in dates],
                          missing + len(present) - len(dates), note=note)

    distinct = len(lowered)
    if distinct <= max(20, int(len(present) * 0.05)) and distinct < len(present):
        return ColumnInfo(name, index, CATEGORICAL, [v.strip() for _, v in present], [n for n, _ in present], missing)
    info = ColumnInfo(name, index, TEXT, [v.strip() for _, v in present], [n for n, _ in present], missing)
    if 0.5 <= len(numeric_ok) / len(present) < _TYPE_THRESHOLD:
        info.note = "mixes numbers and text"
    return info


def _infer_dates(present) -> Tuple[Optional[list], Optional[str]]:
    sample = present[:2000]
    day_first = [(n, _parse_date(v, _DATE_FORMATS_DAY_FIRST)) for n, v in sample]
    month_first = [(n, _parse_date(v, _DATE_FORMATS_MONTH_FIRST)) for n, v in sample]
    day_ok = sum(1 for _, d in day_first if d)
    month_ok = sum(1 for _, d in month_first if d)
    best_ok = max(day_ok, month_ok)
    if best_ok / len(sample) < _TYPE_THRESHOLD:
        return None, None
    formats = _DATE_FORMATS_DAY_FIRST if day_ok >= month_ok else _DATE_FORMATS_MONTH_FIRST
    note = None
    if day_ok == month_ok and any("/" in v or "-" in v[3:] for _, v in sample if not v[:4].isdigit()):
        note = "day/month order is ambiguous; read as day first"
    parsed = [(n, _parse_date(v, formats)) for n, v in present]
    return [(n, d) for n, d in parsed if d], note


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _r(x: float, digits: int = 4):
    if x is None or not math.isfinite(x):
        return None
    if x == int(x) and abs(x) < 1e15:
        return int(x)
    return round(x, digits) if abs(x) < 1 else round(x, 2)


def _quantile(sorted_vals: List[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _numeric_stats(col: ColumnInfo) -> dict:
    vals = col.values
    s = sorted(vals)
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    lo_fence, hi_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = [(v, n) for v, n in zip(vals, col.row_numbers) if iqr > 0 and (v < lo_fence or v > hi_fence)]
    median = _quantile(s, 0.5)
    extreme = sorted(outliers, key=lambda vn: abs(vn[0] - median), reverse=True)[:5]
    return {
        "count": len(vals),
        "sum": _r(math.fsum(vals)),
        "mean": _r(statistics.fmean(vals)),
        "median": _r(median),
        "std_dev": _r(statistics.pstdev(vals)) if len(vals) > 1 else 0,
        "min": _r(s[0]),
        "p25": _r(q1),
        "p75": _r(q3),
        "max": _r(s[-1]),
        "zeros": sum(1 for v in vals if v == 0),
        "negatives": sum(1 for v in vals if v < 0),
        "outliers_iqr": {
            "count": len(outliers),
            "rule": "outside 1.5 x IQR from the quartiles",
            "most_extreme": [{"value": _r(v), "row": n} for v, n in extreme],
        },
    }


def _top_values(values) -> dict:
    counts = Counter(values)
    total = len(values)
    return {
        "distinct": len(counts),
        "top": [{"value": str(v), "count": c, "pct": round(100 * c / total, 1)} for v, c in counts.most_common(MAX_CATEGORIES)],
    }


def _period_key(d: datetime, granularity: str) -> str:
    return d.strftime("%Y-%m") if granularity == "month" else d.strftime("%Y-%m-%d")


def _granularity(dates: List[datetime]) -> str:
    return "month" if (max(dates) - min(dates)).days > 60 else "day"


def _usable_numeric(cols: List[ColumnInfo]) -> List[ColumnInfo]:
    return [c for c in cols if c.kind == NUMERIC and not c.likely_identifier and len(c.values) >= 2]


def profile_table(table: Table, cols: Optional[List[ColumnInfo]] = None) -> dict:
    cols = cols or infer_columns(table)
    n_rows = len(table.rows)
    column_profiles = []
    for c in cols:
        entry = {"name": c.name, "type": c.kind, "missing": c.missing,
                 "missing_pct": round(100 * c.missing / n_rows, 1) if n_rows else 0}
        if c.note:
            entry["note"] = c.note
        if c.likely_identifier:
            entry["likely_identifier"] = True
        if c.kind == NUMERIC:
            entry["stats"] = _numeric_stats(c)
        elif c.kind == DATE:
            entry["range"] = {"min": min(c.values).strftime("%Y-%m-%d"), "max": max(c.values).strftime("%Y-%m-%d"),
                              "span_days": (max(c.values) - min(c.values)).days}
        elif c.kind in (CATEGORICAL, BOOLEAN):
            entry.update(_top_values(c.values))
        elif c.kind == TEXT:
            entry["distinct"] = len(set(c.values))
            entry["avg_length"] = round(statistics.fmean(len(v) for v in c.values), 1)
        column_profiles.append(entry)

    numeric = _usable_numeric(cols)
    dates = [c for c in cols if c.kind == DATE and len(c.values) >= 2]
    categoricals = [c for c in cols if c.kind in (CATEGORICAL, BOOLEAN) and len(set(c.values)) <= 20]

    profile = {
        "dataset": table.name,
        "rows": n_rows,
        "rows_truncated": table.rows_truncated,
        "columns": len(table.columns),
        "columns_truncated": table.columns_truncated,
        "duplicate_rows": n_rows - len({tuple(r) for r in table.rows}),
        "column_profiles": column_profiles,
        "time_series": _time_series(table, dates[:2], numeric[:4]),
        "group_summaries": _group_summaries(table, categoricals[:3], numeric[:4]),
        "correlations": _correlations(table, numeric[:12]),
        "sample_rows": {"columns": table.columns, "rows": table.rows[:8]},
    }
    return profile


def _row_lookup(col: ColumnInfo) -> Dict[int, object]:
    return dict(zip(col.row_numbers, col.values))


def _time_series(table: Table, date_cols, numeric_cols) -> list:
    series = []
    for dc in date_cols:
        gran = _granularity(dc.values)
        by_row = _row_lookup(dc)
        counts = Counter(_period_key(d, gran) for d in dc.values)
        periods = sorted(counts)[-MAX_PERIODS:]
        entry = {"date_column": dc.name, "granularity": gran,
                 "rows_per_period": [{"period": p, "rows": counts[p]} for p in periods], "measures": []}
        for nc in numeric_cols:
            sums, n = defaultdict(float), Counter()
            for row, value in zip(nc.row_numbers, nc.values):
                d = by_row.get(row)
                if d is not None:
                    key = _period_key(d, gran)
                    sums[key] += value
                    n[key] += 1
            keep = [p for p in periods if n[p]]
            entry["measures"].append({
                "column": nc.name,
                "by_period": [{"period": p, "sum": _r(sums[p]), "mean": _r(sums[p] / n[p])} for p in keep],
            })
        series.append(entry)
    return series


def _group_summaries(table: Table, cat_cols, numeric_cols) -> list:
    groups = []
    for cc in cat_cols:
        by_row = _row_lookup(cc)
        for nc in numeric_cols:
            sums, n = defaultdict(float), Counter()
            for row, value in zip(nc.row_numbers, nc.values):
                g = by_row.get(row)
                if g is not None:
                    sums[str(g)] += value
                    n[str(g)] += 1
            top = sorted(sums, key=lambda k: sums[k], reverse=True)[:MAX_CATEGORIES]
            groups.append({"group_by": cc.name, "measure": nc.name,
                           "groups": [{"group": g, "rows": n[g], "sum": _r(sums[g]), "mean": _r(sums[g] / n[g])} for g in top]})
    return groups


def _correlations(table: Table, numeric_cols) -> list:
    results = []
    lookups = [_row_lookup(c) for c in numeric_cols]
    for i in range(len(numeric_cols)):
        for j in range(i + 1, len(numeric_cols)):
            pairs = [(lookups[i][r], lookups[j][r]) for r in lookups[i] if r in lookups[j]]
            if len(pairs) < 10:
                continue
            xs, ys = zip(*pairs)
            try:
                r = statistics.correlation(xs, ys)
            except statistics.StatisticsError:
                continue  # constant column
            if abs(r) >= 0.3:
                results.append({"columns": [numeric_cols[i].name, numeric_cols[j].name], "pearson_r": round(r, 3), "n": len(pairs)})
    return sorted(results, key=lambda x: abs(x["pearson_r"]), reverse=True)[:10]


# ---------------------------------------------------------------------------
# Charts: the model suggests, STEP validates against real columns and computes the data
# ---------------------------------------------------------------------------

def build_chart(table: Table, cols: List[ColumnInfo], suggestion: dict) -> dict:
    by_name = {c.name: c for c in cols}
    title = suggestion.get("title") or "Chart"
    chart_type = suggestion.get("chart_type")
    agg = suggestion.get("aggregation") or "count"
    x = by_name.get(suggestion.get("x_column") or "")
    y = by_name.get(suggestion.get("y_column") or "") if suggestion.get("y_column") else None
    base = {"title": title, "chart_type": chart_type, "x_column": suggestion.get("x_column"),
            "y_column": suggestion.get("y_column"), "aggregation": agg, "rationale": suggestion.get("rationale", "")}

    def fail(reason):
        return {**base, "ok": False, "error": reason}

    if x is None:
        return fail(f"Column '{suggestion.get('x_column')}' is not in the dataset.")
    if suggestion.get("y_column") and y is None:
        return fail(f"Column '{suggestion.get('y_column')}' is not in the dataset.")
    if y is not None and y.kind != NUMERIC and chart_type != "histogram":
        return fail(f"'{y.name}' is not numeric, so it cannot be plotted as a value.")

    if chart_type == "histogram":
        if x.kind != NUMERIC:
            return fail(f"'{x.name}' is not numeric, so it has no distribution to plot.")
        return {**base, "ok": True, **_histogram(x.values)}

    if chart_type == "scatter":
        if x.kind != NUMERIC or y is None:
            return fail("A scatter chart needs two numeric columns.")
        ys = _row_lookup(y)
        points = [(xv, ys[r]) for xv, r in zip(x.values, x.row_numbers) if r in ys]
        step = max(1, len(points) // MAX_CHART_POINTS)
        sampled = points[::step][:MAX_CHART_POINTS]
        return {**base, "ok": True, "points": [{"x": _r(a), "y": _r(b)} for a, b in sampled],
                "note": f"Showing {len(sampled)} of {len(points)} points" if len(sampled) < len(points) else None}

    if chart_type in ("bar", "line"):
        if chart_type == "line" and x.kind != DATE:
            return fail(f"A line chart needs a date column on the x-axis; '{x.name}' is {x.kind}.")
        if chart_type == "bar" and x.kind not in (CATEGORICAL, BOOLEAN, TEXT):
            return fail(f"A bar chart needs a category column on the x-axis; '{x.name}' is {x.kind}.")
        if agg in ("sum", "mean") and y is None:
            return fail(f"Aggregation '{agg}' needs a numeric value column.")
        gran = _granularity(x.values) if x.kind == DATE else None
        keys = {r: (_period_key(v, gran) if gran else str(v)) for r, v in zip(x.row_numbers, x.values)}
        sums, n = defaultdict(float), Counter()
        if y is None or agg == "count":
            for k in keys.values():
                n[k] += 1
            values = {k: n[k] for k in n}
            agg = "count"
        else:
            for r, v in zip(y.row_numbers, y.values):
                if r in keys:
                    sums[keys[r]] += v
                    n[keys[r]] += 1
            values = {k: (sums[k] if agg == "sum" else sums[k] / n[k]) for k in n}
        if chart_type == "line":
            labels = sorted(values)[-MAX_PERIODS:]
        else:
            labels = sorted(values, key=lambda k: values[k], reverse=True)[:MAX_CHART_CATEGORIES]
        note = None
        if chart_type == "bar" and len(values) > MAX_CHART_CATEGORIES:
            note = f"Top {MAX_CHART_CATEGORIES} of {len(values)} categories"
        return {**base, "ok": True, "aggregation": agg, "labels": labels, "values": [_r(values[k]) for k in labels],
                "granularity": gran, "note": note}

    return fail(f"Unsupported chart type '{chart_type}'.")


def _histogram(values: List[float]) -> dict:
    # Bin within the IQR fences so a few extreme values don't squash the chart into one bar
    s = sorted(values)
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    inside = [v for v in values if q1 - 1.5 * iqr <= v <= q3 + 1.5 * iqr] if iqr > 0 else list(values)
    excluded = len(values) - len(inside)
    note = (f"{excluded} outlier{'s' if excluded != 1 else ''} outside {_r(min(inside))}–{_r(max(inside))} not shown"
            if excluded else None)
    lo, hi = min(inside), max(inside)
    if lo == hi:
        return {"labels": [str(_r(lo))], "values": [len(inside)], "note": note or "All values are identical"}
    bins = min(20, max(5, int(math.sqrt(len(inside)))))
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in inside:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    labels = [f"{_fmt(lo + i * width)}–{_fmt(lo + (i + 1) * width)}" for i in range(bins)]
    return {"labels": labels, "values": counts, "note": note}


def _fmt(x: float) -> str:
    # Short bin-edge labels: 3 significant figures
    return f"{x:,.3g}" if abs(x) < 1e6 else f"{x:,.0f}"


# ---------------------------------------------------------------------------
# Grounding check: are the figures the model quotes present in the profile?
# ---------------------------------------------------------------------------

_FIGURE_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def profile_numbers(profile) -> List[float]:
    found = []

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, dict):
            for k, v in node.items():
                if k != "sample_rows":
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(profile)
    return found


def figures_supported(text: str, numbers: List[float]) -> Tuple[bool, List[str]]:
    """True if every figure quoted in text matches a profile number (allowing for rounding)."""
    unmatched = []
    for raw in _FIGURE_RE.findall(text or ""):
        cleaned = raw.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if value.is_integer() and 1900 <= value <= 2100:
            continue  # years and dates
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        tolerance = 0.5 * 10 ** (-decimals)
        if not any(abs(n - value) <= max(tolerance, abs(n) * 0.005) for n in numbers):
            unmatched.append(raw)
    return (not unmatched), unmatched
