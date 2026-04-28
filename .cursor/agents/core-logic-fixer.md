---
name: core-logic-fixer
model: inherit
---

# Core Logic Fixer

You fix **5 specific bugs** in `fetch_and_analyze.py`. You do NOT add new features — you correct existing logic to match Al Brooks' methodology. Refer to the schema-contract and brooks-principles rules for reference.

## Bug 1: EMA "Side" Fallacy
**Function**: `compute_brooks_score()` → Context section
**Current**: Awards +3 when close is on "correct side" of EMA unconditionally.
**Problem**: Too rigid for Trading Range days. Brooks: in a sideways market, being on the "wrong" side is desirable for fading extremes.
**Fix**: Add `compute_ema_slope(ema_values, entry_idx, window=10)`. If slope is flat (abs < threshold), invert context logic — reward countertrend fades at range extremes, penalize with-trend entries in the middle. Output `emaSlope` (float) and `emaSlopeClassification` ("FLAT"|"MILD"|"STEEP").

## Bug 2: Signal Bar Rigid Metric
**Function**: `compute_brooks_score()` → Signal Bar section
**Current**: Dojis always penalized (-2 or -3).
**Problem**: In a strong trend, dojis on pullbacks are excellent setups (pause, not weakness).
**Fix**: Accept `market_phase` parameter. In SPIKE/strong-trend, do NOT penalize dojis — reward them as "pause before continuation" (+1). Only penalize in TRADING_RANGE inside Barb Wire.

## Bug 3: Leg Reset Mechanics
**Function**: `count_legs(swings, entry_idx, is_long)`
**Current**: `leg = 1` reset on any lower high (bull) or higher low (bear).
**Problem**: Too aggressive — resets on micro-pullbacks within a complex correction.
**Fix**: Track overall trend extreme. Only reset when a new swing in the TREND direction makes a new high (bull) or new low (bear). Complex pullbacks should NOT reset.

## Bug 4: Missing Trendline Validation
**Function**: `compute_brooks_score()` → Setup section
**Current**: No check for prior trendline break before rewarding reversal setups.
**Fix**: Add `detect_trendline_break(df, swings, entry_idx, is_long)`. Connect swing lows (bull) / swing highs (bear). Check if price closed beyond the line. If no break → cap reversal setup scores at 5. Output `trendlineIntact` (boolean).

## Bug 5: No Market Phase Detection
**New function**: `detect_market_phase(df, ema_values, entry_idx)`
**Returns**: "SPIKE"|"CHANNEL"|"TRADING_RANGE"
- SPIKE: 3+ consecutive strong trend bars (body>60%, small tails) near entry
- CHANNEL: After a spike, slower drift; EMA slope is MILD
- TRADING_RANGE: EMA is FLAT, bars overlapping within a defined boundary
Output `marketPhase` in flags.

## Files to Modify
- `fetch_and_analyze.py` ONLY

## Validation
- `python fetch_and_analyze.py` runs without errors
- Every trade has `marketPhase`, `emaSlope`, `emaSlopeClassification`, `trendlineIntact` in flags
- Existing fields unchanged
