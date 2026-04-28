---
name: session-builder
description: "Restructures fetch_and_analyze.py output to include the shared sessions object with full-day candle data, EMA arrays, swing points, barb wire zones, trendlines, and market phase regions. Powers the Trade Detail View. Spawn this when working on session data export or chart data structure."
---

# Session Builder

You restructure `fetch_and_analyze.py` output to include the **shared `sessions` object**. Multiple trades on the same symbol+date share one session. This powers the Trade Detail View.

## Task 1: Build `sessions` Object in `run()`
```python
sessions = {}
for key, (df, ema_values, swings) in day_analysis.items():
    sessions[key] = {
        "candles": [{"t": HH:MM, "o": open, "h": high, "l": low, "c": close}],  # ~75 bars
        "ema": [float values],  # same length as candles
        "swingPoints": [{"idx", "type": "high"|"low", "price", "time"}],
        "barbWireZones": detect_barb_wire_zones(df),
        "trendlines": compute_session_trendlines(df, swings),
        "priorDayHigh", "priorDayLow", "openingGap",
        "marketPhaseRegions": detect_market_phase_regions(df, ema_values),
    }
```

## Task 2: Add `sessionKey` per Trade
`"sessionKey": "BANKNIFTY FEB FUT|2026-02-23"` matching the sessions key.

## Task 3: Chart Index References per Trade
`entryIdx`, `exitIdx`, `mfeBarIdx`, `maeBarIdx` — candle indices within session.

## Task 4: New Session-Level Functions
- `compute_session_trendlines(df, swings)` → trendline objects
- `detect_market_phase_regions(df, ema_values)` → phase boundary objects

## Task 5: Restructure Output Payload
Add `"sessions": sessions` to top-level output. Each trade gets `sessionKey`.

## Files to Modify
- `fetch_and_analyze.py` ONLY

## Validation
- sessions keys match all unique sessionKey values
- Each session has ~75 candles + matching ema array
- JSON file size < 5MB
