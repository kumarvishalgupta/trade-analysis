---
name: indicator-builder
description: "Adds 12 new detection functions to fetch_and_analyze.py for Brooks Price Action indicators: failed final flag, functional legs, shrinking stairs, pressure score, vacuum magnet, breakout test, gap bar with 2HM, shaved bar context, always-in status, prior day levels, spike origin, and barb wire zones. Spawn this when adding new indicator detection functions to the Python backend."
---

# Indicator Builder

You add **12 new detection functions** to `fetch_and_analyze.py`. Each is self-contained and stores results in the trade's `flags` object per the schema-contract rule. You assume core-logic-fixer's work (market phase, EMA slope, trendline) is already done.

## Functions to Implement

### 1. `detect_failed_final_flag(df, swings, entry_idx, is_long)` → `isFailedFinalFlag: bool`
Horizontal pause (ii pattern or small TTR) late in trend that breaks out and reverses within 1-5 bars. Also: 1-2 bars after a climactic large trend bar.

### 2. `detect_functional_legs(df, swings, entry_idx, is_long)` → `isFunctionalLeg: bool, functionalLegType: str|None`
Large tail on pullback bar = second leg on smaller timeframe. Apparent H1 where tail dips significantly = Functional H2. Returns type "H1→H2" or "L1→L2".

### 3. `detect_shrinking_stairs(df, swings, entry_idx)` → `isShrinkingStairs: bool, stairDistances: list`
3+ trending swings with decreasing breakout distances = waning momentum.

### 4. `compute_pressure_score(df, entry_idx, lookback=20)` → `pressureScore: int (0-10), consecutiveTrendBars: int, shavedBarFrequency: float`
Count consecutive trend bars, shaved bar frequency, close-at-extreme ratio.

### 5. `detect_vacuum_magnet(df, entry_idx, entry_price, pdh, pdl, gap, mm)` → `vacuumMagnet: str|None, vacuumDistance: float`
Distance to nearest price magnet (PDH, PDL, Gap, MM). Returns which magnet and distance in points.

### 6. `detect_breakout_test(df, swings, entry_idx, entry_price)` → `isBreakoutTest: bool, breakoutTestLevel: float|None`
Pullback testing exact price of a prior breakout.

### 7. `detect_gap_bar_with_2hm(df, ema_values, entry_idx, two_hm_minutes)` → `gapBarWith2HM: bool`
Existing gap bar detection + 2HM ≥ 120 min validation.

### 8. `classify_shaved_bar_context(df, swings, entry_idx, is_shaved, is_long)` → `shavedBarLeg: int, shavedBarIsExhaustion: bool`
Leg 1 = urgency (good). Leg 3+ = exhaustion (trap).

### 9. `detect_always_in_status(df, ema_values, swings, entry_idx)` → `alwaysInStatus: str`
"LONG"|"SHORT"|"NEUTRAL" based on last major breakout direction.

### 10. `compute_prior_day_levels(all_candles, symbol, trade_date)` → `priorDayHigh, priorDayLow, openingGap`
Look up prior session's high/low. Compute opening gap.

### 11. `detect_spike_origin(df, ema_values, entry_idx, market_phase)` → `spikeOriginPrice: float|None`
If CHANNEL phase, identify the channel origin price (spike start) as a price magnet.

### 12. `detect_barb_wire_zones(df)` → `list of {startIdx, endIdx, startTime, endTime}`
Session-level continuous barb wire zone detection. Stored in the `sessions` object.

## Integration
Wire all 12 into `analyze_trade()`. Store in `flags` per schema-contract.

## Files to Modify
- `fetch_and_analyze.py` ONLY

## Validation
- All new flags appear in output JSON
- Functions are pure (deterministic, no side effects)
- `python fetch_and_analyze.py` runs without errors
