"""
Deterministic DCF layer: historical FCF-driver derivation + valuation recompute.

Pure compute — no LLM, no audit, no Excel. Two responsibilities, one file, so
the derivation the LLM sees and the arithmetic the sanity rule re-runs can never
disagree:

  derive_fcf_drivers — given multi-year structure mappings from the analysis
      server's DCF Pass A (which rows are Revenue / EBIT / Taxes / D&A / CapEx /
      ΔNWC, which columns are historical years) plus the raw grids, extract the
      per-year series and the driver ratios the template forecasts with
      (revenue growth, EBIT margin, tax % of EBIT, D&A / CapEx / ΔNWC as % of
      sales). The LLM never produces a figure — it only locates structure.

  recompute_dcf — re-run the whole valuation (CAPM WACC → driver forecast →
      unlevered FCF → PVs → Gordon terminal value → EV → equity bridge) from an
      apply_dcf_template call's DECLARED metadata. DcfSanityRule uses this to
      check terminal-value dominance and that the declared WACC matches its own
      CAPM components — declared-vs-actual, the forecast_integrity philosophy.

Sign conventions (stated, so they're auditable):
- EBIT, Taxes, D&A, CapEx series are stored as positive magnitudes (abs), the
  way the template rows present them.
- dnwc is the INCREASE in net working capital, cash outflow positive, so
  FCF = EBIAT + D&A − CapEx − ΔNWC holds with all-positive healthy-year inputs.
  A CF "changes in operating assets and liabilities" line is a cash-flow
  IMPACT (inflow positive), so it is negated on the way in; a BS-derived
  Δ(receivables + inventory − payables) is already an NWC increase.
"""
from typing import Any, Dict, List, Optional

from variance import _to_number


# Component roles this layer reads, per statement. `change_in_nwc` is a
# DCF-pass-only CF role (the variance vocabulary doesn't need it).
_SERIES_SOURCES = {
    "revenue": (("IS", "revenue"),),
    "ebit": (("IS", "operating_profit"),),
    "taxes": (("IS", "tax"),),
    "dna": (("CF", "depreciation_amortisation"), ("IS", "depreciation_amortisation")),
    "capex": (("CF", "capex"),),
}
_BS_NWC_ROLES = ("receivables", "inventory", "payables")


def _role_row(mapping: dict, role: str) -> Optional[int]:
    """role → 0-based grid row from a DCF Pass A mapping. First occurrence wins."""
    for item in (mapping or {}).get("line_items", []) or []:
        if str(item.get("role", "") or "").strip() == role:
            try:
                return int(item.get("row"))
            except (TypeError, ValueError):
                return None
    return None


def _cell_at(grid: List[List[Any]], row: Optional[int], col: Optional[int]) -> Optional[float]:
    """Numeric value at (row, col), or None if out of range / non-numeric."""
    if row is None or col is None or not (0 <= row < len(grid)):
        return None
    row_vals = grid[row] or []
    if not (0 <= col < len(row_vals)):
        return None
    return _to_number(row_vals[col])


def _year_key(label: Any) -> Optional[int]:
    """Pull a comparable year number out of a column label ("CY '17", "2017",
    "Dec '17"). Two-digit years are read as 20xx. None when no digits found."""
    import re
    digits = re.findall(r"\d+", str(label or ""))
    if not digits:
        return None
    n = int(digits[-1])
    return 2000 + n if n < 100 else n


def _align_years(mappings: Dict[str, dict]) -> Optional[List[int]]:
    """Year keys present in EVERY supplied statement (sorted ascending), so
    cross-statement series line up by actual year, not by column position.
    Falls back to the primary (IS) years when labels don't parse everywhere."""
    per_stmt: List[set] = []
    for mapping in mappings.values():
        keys = {k for k in (_year_key(l) for l in mapping.get("year_labels", [])) if k is not None}
        if keys:
            per_stmt.append(keys)
    if not per_stmt:
        return None
    common = set.intersection(*per_stmt)
    return sorted(common) if common else None


def _cols_for_years(mapping: dict, years: List[int]) -> List[Optional[int]]:
    """Map each aligned year to the statement's column index (None if absent)."""
    by_year = {}
    for label, col in zip(mapping.get("year_labels", []), mapping.get("year_cols", [])):
        k = _year_key(label)
        if k is not None and k not in by_year:
            try:
                by_year[k] = int(col)
            except (TypeError, ValueError):
                continue
    return [by_year.get(y) for y in years]


def _pct_series(num: List[Optional[float]], den: List[Optional[float]]) -> List[Optional[float]]:
    return [
        (n / d) if (n is not None and d not in (None, 0)) else None
        for n, d in zip(num, den)
    ]


def _growth_series(vals: List[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for prev, cur in zip(vals, vals[1:]):
        out.append((cur / prev - 1.0) if (prev not in (None, 0) and cur is not None) else None)
    return out


def derive_fcf_drivers(
    mappings: Dict[str, dict],
    grids: Dict[str, List[List[Any]]],
    sheets: Optional[Dict[str, str]] = None,
) -> dict:
    """Extract historical FCF components and driver ratios from located grids.

    `mappings` maps statement role ("IS"|"BS"|"CF") → DCF Pass A mapping:
        {"year_cols": [int], "year_labels": [str], "line_items": [{label, row, role}]}
    (historical/actual year columns only — the Pass A prompt excludes estimate
    columns). `grids` maps the same roles → used-range values; `sheets` → sheet
    names, for provenance.

    Returns {years, series, drivers, cash, debt, provenance, warnings}:
      series  — {revenue, ebit, taxes, dna, capex, dnwc}: per-year lists aligned
                to `years` (None where a year is missing); dnwc has one entry
                per year-pair (NWC increase, outflow positive)
      drivers — {revenue_growth, ebit_margin, tax_pct_ebit, dna_pct_sales,
                 capex_pct_sales, dnwc_pct_sales}: historical ratio series the
                 LLM bases its forecast-driver suggestions on
      provenance — component → {statement, sheet, row (0-based), cols (0-based)}
      warnings — named coverage gaps ("capex: no Cash Flow statement supplied"),
                 never silent degradation

    The IS is required (revenue + EBIT are the model's spine); everything else
    degrades with a warning.
    """
    sheets = sheets or {}
    warnings: List[str] = []

    is_mapping = mappings.get("IS")
    if not is_mapping:
        return {"error": "Income Statement structure is required for FCF derivation.",
                "warnings": warnings}

    years = _align_years(mappings)
    if not years:
        # Labels didn't parse across statements — fall back to the IS's own columns.
        years = [k for k in (_year_key(l) for l in is_mapping.get("year_labels", [])) if k is not None]
        if len(mappings) > 1:
            warnings.append(
                "year labels could not be aligned across statements — using the "
                "Income Statement's years only"
            )
    if len(years) < 2:
        return {"error": "Fewer than 2 usable historical years — a DCF needs a trend.",
                "warnings": warnings}

    cols = {role: _cols_for_years(m, years) for role, m in mappings.items()}

    def read_series(component: str) -> Optional[List[Optional[float]]]:
        for stmt, role in _SERIES_SOURCES[component]:
            mapping = mappings.get(stmt)
            if not mapping:
                continue
            row = _role_row(mapping, role)
            if row is None:
                continue
            stmt_cols = cols[stmt]
            grid = grids.get(stmt) or []
            vals = [_cell_at(grid, row, c) for c in stmt_cols]
            if any(v is not None for v in vals):
                provenance[component] = {
                    "statement": stmt, "sheet": sheets.get(stmt, ""),
                    "row": row, "cols": stmt_cols,
                }
                return vals
        return None

    provenance: Dict[str, dict] = {}
    series: Dict[str, Optional[List[Optional[float]]]] = {}
    for component in ("revenue", "ebit", "taxes", "dna", "capex"):
        vals = read_series(component)
        if vals is None:
            src = " or ".join(sorted({s for s, _ in _SERIES_SOURCES[component]}))
            warnings.append(f"{component}: not located (needs {src})")
            series[component] = None
        else:
            # Positive magnitudes — expense/outflow rows are negative in many layouts.
            series[component] = [abs(v) if v is not None else None for v in vals]
            n_gaps = sum(1 for v in vals if v is None)
            if n_gaps:
                # A gap mid-series must be SAID: the declared history the LLM
                # echoes back skips the gap, so without this warning a sparse
                # year would pass the provenance check unremarked.
                warnings.append(
                    f"{component}: no value in {n_gaps} of {len(vals)} historical years"
                )

    if series.get("revenue") is None or series.get("ebit") is None:
        return {"error": "Revenue and EBIT rows are required on the Income Statement.",
                "warnings": warnings}

    # ── ΔNWC: CF line preferred (negated: cash impact → NWC increase), BS fallback ──
    dnwc: Optional[List[Optional[float]]] = None
    cf_mapping = mappings.get("CF")
    if cf_mapping:
        row = _role_row(cf_mapping, "change_in_nwc")
        if row is not None:
            grid = grids.get("CF") or []
            vals = [_cell_at(grid, row, c) for c in cols["CF"]]
            if any(v is not None for v in vals):
                # Per-year cash impact of working-capital changes; drop the first
                # year so the series aligns with year-pairs like the BS route.
                dnwc = [(-v if v is not None else None) for v in vals][1:]
                provenance["dnwc"] = {
                    "statement": "CF", "sheet": sheets.get("CF", ""),
                    "row": row, "cols": cols["CF"],
                }
    bs_mapping = mappings.get("BS")
    if dnwc is None and bs_mapping:
        parts = {}
        for role in _BS_NWC_ROLES:
            row = _role_row(bs_mapping, role)
            if row is not None:
                grid = grids.get("BS") or []
                parts[role] = [_cell_at(grid, row, c) for c in cols["BS"]]
        if "receivables" in parts or "inventory" in parts:
            def nwc_at(i: int) -> Optional[float]:
                total = 0.0
                for role, sign in (("receivables", 1), ("inventory", 1), ("payables", -1)):
                    vals = parts.get(role)
                    if vals is None:
                        continue
                    if vals[i] is None:
                        return None
                    total += sign * abs(vals[i])
                return total
            levels = [nwc_at(i) for i in range(len(years))]
            dnwc = [
                (cur - prev) if (cur is not None and prev is not None) else None
                for prev, cur in zip(levels, levels[1:])
            ]
            provenance["dnwc"] = {
                "statement": "BS", "sheet": sheets.get("BS", ""),
                "rows": {r: _role_row(bs_mapping, r) for r in _BS_NWC_ROLES
                         if _role_row(bs_mapping, r) is not None},
                "cols": cols["BS"],
            }
    if dnwc is None:
        warnings.append("dnwc: not located (needs a CF working-capital line or "
                        "BS receivables/inventory/payables)")
    series["dnwc"] = dnwc

    # ── Cash & debt for the equity bridge (latest year, BS) ──
    cash = debt = None
    if bs_mapping:
        for name in ("cash", "debt"):
            row = _role_row(bs_mapping, name)
            if row is not None:
                grid = grids.get("BS") or []
                last_col = next((c for c in reversed(cols["BS"]) if c is not None), None)
                if last_col is not None:
                    v = _cell_at(grid, row, last_col)
                    if v is not None:
                        if name == "cash":
                            cash = abs(v)
                        else:
                            debt = abs(v)
                        provenance[name] = {
                            "statement": "BS", "sheet": sheets.get("BS", ""),
                            "row": row, "cols": [last_col],
                        }
    if cash is None:
        warnings.append("cash: not located on the Balance Sheet — equity bridge needs it")
    if debt is None:
        warnings.append("debt: not located on the Balance Sheet — equity bridge needs it")

    revenue = series["revenue"]
    drivers = {
        "revenue_growth": _growth_series(revenue),
        "ebit_margin": _pct_series(series["ebit"], revenue),
        "tax_pct_ebit": _pct_series(series["taxes"], series["ebit"]) if series.get("taxes") else None,
        "dna_pct_sales": _pct_series(series["dna"], revenue) if series.get("dna") else None,
        "capex_pct_sales": _pct_series(series["capex"], revenue) if series.get("capex") else None,
        # dnwc is per year-pair; ratio against the LATER year's revenue of each pair.
        "dnwc_pct_sales": _pct_series(dnwc, revenue[1:]) if dnwc else None,
    }

    return {
        "years": years,
        "series": series,
        "drivers": drivers,
        "cash": cash,
        "debt": debt,
        "provenance": provenance,
        "warnings": warnings,
    }


def recompute_dcf(args: dict) -> dict:
    """Re-run the valuation from an apply_dcf_template call's declared metadata.

    Everything here is the same arithmetic the written sheet performs, so the
    sanity rule can compare declared assumptions against their consequences
    without trusting a single grid formula. Returns {} keys only for what the
    declared args allow; never raises on missing/garbage input — callers treat
    an absent key as "could not check".

    Keys (when computable): capm_wacc, forecast_revenue, forecast_fcf, pv_fcf,
    terminal_value, pv_terminal_value, enterprise_value, tv_share_of_ev,
    equity_value, implied_share_price.
    """
    out: dict = {}

    def num(v: Any) -> Optional[float]:
        return _to_number(v)

    comp = args.get("wacc_components") or {}
    rf, beta, mrp = num(comp.get("rf")), num(comp.get("beta")), num(comp.get("mrp"))
    kd, tax = num(comp.get("cost_of_debt")), num(comp.get("tax_rate"))
    debt_v, equity_v = num(comp.get("debt")), num(comp.get("equity"))
    if None not in (rf, beta, mrp, kd, tax, debt_v, equity_v) and (debt_v + equity_v) > 0:
        ke = rf + beta * mrp
        wd = debt_v / (debt_v + equity_v)
        out["capm_wacc"] = wd * kd * (1 - tax) + (1 - wd) * ke

    wacc = num(args.get("wacc"))
    tgr = num(args.get("terminal_growth"))
    drivers = args.get("drivers") or {}
    growth = [num(g) for g in (drivers.get("revenue_growth") or [])]
    margin = [num(m) for m in (drivers.get("ebit_margin") or [])]
    tax_pct = [num(t) for t in (drivers.get("tax_pct_ebit") or [])]
    dna_pct = [num(x) for x in (drivers.get("dna_pct_sales") or [])]
    capex_pct = [num(x) for x in (drivers.get("capex_pct_sales") or [])]
    dnwc_pct = [num(x) for x in (drivers.get("dnwc_pct_sales") or [])]

    hist = args.get("historical") or {}
    rev_hist = [num(v) for v in (hist.get("revenue") or [])]
    base_rev = next((v for v in reversed(rev_hist) if v is not None), None)

    n = len(growth)
    aligned = all(len(s) == n for s in (margin, tax_pct, dna_pct, capex_pct, dnwc_pct))
    if (base_rev is not None and n > 0 and aligned
            and all(v is not None for s in (growth, margin, tax_pct, dna_pct, capex_pct, dnwc_pct) for v in s)):
        revenue, fcf = [], []
        rev = base_rev
        for i in range(n):
            rev = rev * (1 + growth[i])
            ebit = margin[i] * rev
            taxes = tax_pct[i] * ebit
            ebiat = ebit - taxes
            f = ebiat + dna_pct[i] * rev - capex_pct[i] * rev - dnwc_pct[i] * rev
            revenue.append(rev)
            fcf.append(f)
        out["forecast_revenue"] = revenue
        out["forecast_fcf"] = fcf

        if wacc is not None and wacc > 0:
            pv = [f / (1 + wacc) ** (i + 1) for i, f in enumerate(fcf)]
            out["pv_fcf"] = pv
            if tgr is not None and wacc > tgr:
                tv = fcf[-1] * (1 + tgr) / (wacc - tgr)
                pv_tv = tv / (1 + wacc) ** n
                ev = sum(pv) + pv_tv
                out["terminal_value"] = tv
                out["pv_terminal_value"] = pv_tv
                out["enterprise_value"] = ev
                if ev != 0:
                    out["tv_share_of_ev"] = pv_tv / ev
                cash, debt_b = num(hist.get("cash")), num(hist.get("debt"))
                if cash is not None and debt_b is not None:
                    eq = ev + cash - debt_b
                    out["equity_value"] = eq
                    shares = num(args.get("shares"))
                    if shares:
                        out["implied_share_price"] = eq / shares
    return out
