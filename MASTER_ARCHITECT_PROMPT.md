# Trade Integrity Dashboard — Multi-Agent Upgrade System

> **Architecture**: 1 Orchestrator + 8 Sub-Agents, each with focused context windows.
> **Problem Solved**: An 844-line monolithic prompt causes context degradation. This structure ensures each agent holds only what it needs (~150-250 lines), with the Schema Contract as the binding handshake between all agents.

---

## HOW TO USE THIS PROMPT

### Execution Model
Run agents **sequentially** in the order below. Each agent produces a verifiable artifact that feeds the next. The Orchestrator section tells you what to pass to each agent and what to validate before moving on.

### Per-Agent Workflow
1. Copy the **ORCHESTRATOR** section into a new conversation — this is your control panel
2. For each phase, copy the relevant **AGENT** prompt into a new conversation (or the same one if context allows)
3. Always include **AGENT 0's output** (the Schema Contract) with every subsequent agent — it's the universal reference
4. After each agent completes, run the **validation checklist** before proceeding

### Context Budget per Agent
| Agent | Approx. Lines | Focus |
|-------|--------------|-------|
| 0 - Schema | ~180 | JSON contract only |
| 1 - Core Fixes | ~150 | 5 bug fixes in Python |
| 2 - Indicators | ~200 | 12 new Python functions |
| 3 - Scoring | ~150 | Scoring rewrite in Python |
| 4 - Sessions | ~150 | Session export + metadata in Python |
| 5 - Card UI | ~200 | React card-level upgrades |
| 6 - Detail View | ~250 | React modal + candlestick chart |
| 7 - Anti-Mistake | ~200 | React learning system |

---

# 🎯 ORCHESTRATOR — Master Control

You are the **Orchestrator** of a multi-agent system upgrading a Trade Integrity Dashboard. You manage 8 sub-agents that each handle a focused piece of the upgrade. Your job:

1. **Dispatch** — Send each agent its prompt + the Schema Contract
2. **Validate** — Check each agent's output against the validation criteria before proceeding
3. **Pass Artifacts** — Carry the output of completed agents as context for dependent agents
4. **Resolve Conflicts** — If an agent's output contradicts the Schema Contract, reject and re-prompt

## System Overview (Carry This Into Every Agent)

```
SYSTEM: Trade Integrity Dashboard for NSE index futures (BANKNIFTY, NIFTY)
STACK:  Python backend (fetch_and_analyze.py) → enriched_trades.json → React frontend (App.jsx)
DATA:   5-min OHLC candles, 20-period EMA, 9:15-15:30 IST, cached in candle_cache/
INPUT:  TradesFromPDFs.csv — 40 pre-paired round-trip trades, Feb 23 – Mar 13 2026
DEPS:   Python: numpy, pandas, stdlib only | React: Tailwind CSS, no charting libraries (SVG/Canvas only)
```

## Execution DAG (Dependency Order)

```
Agent 0: Schema Architect ─────────────────────────┐
    │                                                │
    ▼                                                │ (Schema Contract
Agent 1: Core Logic Fixer (Python)                   │  is passed to
    │                                                │  ALL subsequent
    ▼                                                │  agents)
Agent 2: Indicator Builder (Python)                  │
    │                                                │
    ▼                                                │
Agent 3: Scoring Engine (Python)                     │
    │                                                │
    ▼                                                │
Agent 4: Session Builder (Python) ──────────────────┘
    │
    ├──────────────────┐
    ▼                  ▼
Agent 5: Card UI    Agent 6: Detail View    ← (can run in parallel)
    │                  │
    ▼                  ▼
Agent 7: Anti-Mistake Learning System      ← (depends on 5 + 6)
```

## Validation Protocol (After Each Agent)

| Check | How |
|-------|-----|
| **Schema compliance** | Every new field in the output JSON matches Agent 0's contract exactly |
| **No regressions** | Existing fields unchanged unless explicitly marked for modification |
| **File scope** | Agent only modified the files listed in its brief |
| **No new deps** | No `pip install` or `npm install` additions |
| **Runnable** | `python fetch_and_analyze.py` executes without errors (after backend agents) |
| **Renders** | `npm run dev` in dashboard/ shows no console errors (after frontend agents) |

---

# AGENT 0 — Schema Architect

## Role
You are the **Schema Architect**. You define the complete JSON data contract between the Python backend and the React frontend. Every other agent must conform to your output. You do NOT write code — you produce the canonical reference schema.

## Your Context

### Current Output Shape (enriched_trades.json)
```json
{
  "generatedAt": "ISO timestamp",
  "broker": "zerodha|dhan|yfinance",
  "csvFile": "TradesFromPDFs.csv",
  "dateRange": "YYYY-MM-DD_to_YYYY-MM-DD",
  "totalTrades": 40,
  "realDataTrades": 40,
  "trades": [ ...trade objects... ]
}
```

### Current Per-Trade Object
```json
{
  "tradeNum": 1,
  "symbol": "BANKNIFTY FEB FUT",
  "side": "SHORT",
  "entryTime": "2026-02-23 12:53:42",
  "exitTime": "2026-02-23 13:14:53",
  "qty": 30,
  "entryPrice": 61118.2,
  "exitPrice": 61090.0,
  "pnlPoints": 28.2,
  "pnlRupees": 846.0,
  "duration": "21m 11s",
  "metrics": { "mfe": 42.30, "mae": 36.25, "mfePct": 0.069, "maePct": 0.059 },
  "score": { "total": 7, "context": 8, "setupReliability": 8, "signalBar": 5, "institutional": 5 },
  "flags": {
    "isBarbWire": false, "twoHM": 10, "isShavedBar": false, "isGapBar": false,
    "leg": 2, "isFailedFailure": false, "isInstitutionalTrap": false,
    "isStopRun": false, "isMiddleOfMiddle": false, "withTrend": true,
    "reachedMM": false, "measuredMoveTarget": 56.4, "pnl": 846.0
  },
  "analogy": "string",
  "signalBar": { "open": 0, "high": 0, "low": 0, "close": 0, "isDoji": false, "isStrongTrend": false, "isShaved": false },
  "emaAtEntry": 61162.66,
  "sparklineCandles": [{"t":"HH:MM","o":0,"h":0,"l":0,"c":0}],
  "dataSource": "real|csv_only"
}
```

## Your Task

Produce the **complete** upgraded schema with these additions:

### 1. New top-level `sessions` object
Multiple trades share the same session (symbol+date). Store full-day chart data ONCE per session, not per trade.
- Full-day candle array (~75 bars, 9:15-15:30 IST)
- Full EMA array (20-period, same length as candles)
- Swing points array (idx, type high/low, price, time)
- Barb Wire zone boundaries (startIdx, endIdx, startTime, endTime)
- Trendline objects (type bull/bear, start/end idx+price, broken flag, brokenAtIdx)
- Prior day high/low, opening gap
- Market phase region boundaries (phase SPIKE/CHANNEL/TRADING_RANGE, startIdx, endIdx)

### 2. New per-trade fields
- `sessionKey`: string reference to the sessions object (e.g., "BANKNIFTY FEB FUT|2026-02-23")
- `entryIdx`, `exitIdx`: candle indices within the session
- `mfeBarIdx`, `maeBarIdx`: which candle had the best/worst excursion

### 3. Expanded `flags` object — add all of these:
- `marketPhase`: "SPIKE"|"CHANNEL"|"TRADING_RANGE"
- `alwaysInStatus`: "LONG"|"SHORT"|"NEUTRAL"
- `trendlineIntact`: boolean
- `trendlineBrokenAt`: string|null (timestamp)
- `isFailedFinalFlag`: boolean
- `isFunctionalLeg`: boolean
- `functionalLegType`: "H1→H2"|"L1→L2"|null
- `isShrinkingStairs`: boolean
- `stairDistances`: number[] (breakout distances per swing)
- `pressureScore`: number (0-10)
- `consecutiveTrendBars`: number
- `shavedBarFrequency`: number (ratio in recent window)
- `vacuumMagnet`: "PDH"|"PDL"|"GAP"|"MM"|null
- `vacuumDistance`: number (points to nearest magnet)
- `isBreakoutTest`: boolean
- `breakoutTestLevel`: number|null
- `gapBarWith2HM`: boolean
- `shavedBarLeg`: number
- `shavedBarIsExhaustion`: boolean
- `emaSlope`: number
- `emaSlopeClassification`: "FLAT"|"MILD"|"STEEP"
- `spikeOriginPrice`: number|null
- `priorDayHigh`: number
- `priorDayLow`: number
- `openingGap`: number|null

### 4. Expanded `score` object — add:
- `pressure`: number (0-10, new 6th component)

### 5. New `scoreDecomposition` object per trade
Each score component gets: `{ score: number, reasons: string[] }`
Components: context, setupReliability, signalBar, institutional, pressure

### 6. Anti-mistake metadata per trade
- `tradeQuality`: "GOOD_WIN"|"GOOD_LOSS"|"BAD_WIN"|"BAD_LOSS"
- `mistakeTag`: string ("#FOMO", "#LOW_CONTEXT", "#BARB_WIRE", "#REVENGE", "#OVEREXTENDED", "#GOOD_ENTRY", "#PERFECT_STORM")
- `thesis`: string (auto-generated: what the setup predicted)
- `reality`: string (auto-generated: what actually happened post-entry)
- `profitLeftOnTable`: number (MFE minus actual P&L in points)
- `maeAsStopPct`: number (MAE as % of reasonable stop distance)

## Output
Produce the complete JSON schema with TypeScript-style type annotations, including the top-level structure, the sessions object, and the per-trade object. This output becomes the **Schema Contract** that is passed to ALL subsequent agents.

## Validation
- Every field has an explicit type
- All existing fields are preserved (backward compatible)
- Sessions structure avoids data duplication (candle arrays shared)
- No ambiguous types (no `any`)

---

# AGENT 1 — Core Logic Fixer

## Role
You fix **5 specific bugs** in the existing Python analysis functions in `fetch_and_analyze.py`. You do NOT add new features — you correct existing logic to match Al Brooks' methodology.

## Your Context
You will receive: the **Schema Contract** from Agent 0, plus the current function implementations (listed below).

### Bug 1: EMA "Side" Fallacy
**File**: `fetch_and_analyze.py` → `compute_brooks_score()` Context section
**Current**: Awards +3 when close is on "correct side" of EMA unconditionally.
**Problem**: Too rigid for Trading Range days. Brooks: in a sideways market, being on the "wrong" side is desirable for fading extremes.
**Fix**: Add `compute_ema_slope(ema_values, window=10)` function. If slope is flat (< threshold), invert context logic — reward countertrend fades at range extremes, penalize with-trend entries in the middle. Add `emaSlope` and `emaSlopeClassification` ("FLAT"|"MILD"|"STEEP") to the trade output.

### Bug 2: Signal Bar Rigid Metric
**File**: `fetch_and_analyze.py` → `compute_brooks_score()` Signal Bar section
**Current**: `is_strong_trend_bar()` rewards body > 60%; dojis always penalized.
**Problem**: In a strong trend, dojis on pullbacks are excellent setups (pause, not weakness).
**Fix**: Make doji handling **phase-aware**. Accept a `market_phase` parameter. In SPIKE/strong-trend, do NOT penalize dojis — reward them as "pause before continuation." Only penalize in TRADING_RANGE inside Barb Wire.

### Bug 3: Leg Reset Mechanics
**File**: `fetch_and_analyze.py` → `count_legs()`
**Current**: `leg = 1` reset on any lower high (bull) or higher low (bear).
**Problem**: Too aggressive. Brooks resets only on new TREND extremes, not micro-pullbacks.
**Fix**: Track the overall trend extreme. Only reset when a new swing in the TREND direction establishes a new high (bull) or new low (bear). Complex pullbacks with multiple smaller swings should NOT reset the count.

### Bug 4: Missing Trendline Validation
**File**: `fetch_and_analyze.py` → `compute_brooks_score()` Setup section
**Current**: No check for prior trendline break before rewarding reversal setups.
**Problem**: Brooks: you CANNOT trade a reversal without a prior trendline break.
**Fix**: Add `detect_trendline_break(df, swings, entry_idx, is_long)` function. Connect swing lows in bull / swing highs in bear. Check if price closed beyond the line. If no break occurred, cap reversal-oriented setup scores at 5/10. Add `trendlineIntact` boolean to output.

### Bug 5: No Market Phase Detection
**File**: `fetch_and_analyze.py` → new function
**Current**: All bars treated equally regardless of market structure.
**Fix**: Add `detect_market_phase(df, ema_values, entry_idx)` returning "SPIKE"|"CHANNEL"|"TRADING_RANGE".
- SPIKE: 3+ consecutive strong trend bars (body>60%, small tails) near entry
- CHANNEL: After a spike, slower drift within parallel lines; EMA slope is mild
- TRADING_RANGE: EMA is flat, bars overlapping within a defined boundary

## Existing Functions You Will Modify
```python
def compute_ema(closes, period=20): ...           # DO NOT CHANGE
def is_doji(candle, threshold_pct=0.1): ...       # DO NOT CHANGE
def is_strong_trend_bar(candle): ...              # DO NOT CHANGE
def count_legs(swings, entry_idx, is_long): ...   # FIX: Bug 3
def compute_brooks_score(trade, df, ema_values, swings, entry_idx, all_trades, trade_idx): ... # FIX: Bugs 1, 2, 4
```

## New Functions You Will Add
```python
def compute_ema_slope(ema_values, entry_idx, window=10): ...
def detect_market_phase(df, ema_values, entry_idx): ...
def detect_trendline_break(df, swings, entry_idx, is_long): ...
```

## Output
Modified `fetch_and_analyze.py` with the 5 fixes applied. New fields in trade output must match the Schema Contract.

## Validation
- `python fetch_and_analyze.py` runs without errors
- Every trade now has `marketPhase`, `emaSlope`, `emaSlopeClassification`, `trendlineIntact` in flags
- Existing fields unchanged (backward compatible)
- No new pip dependencies

---

# AGENT 2 — Indicator Builder

## Role
You add **12 new detection functions** to `fetch_and_analyze.py`. Each function is self-contained and returns data that gets stored in the trade's `flags` object per the Schema Contract.

## Your Context
You will receive: the **Schema Contract** from Agent 0, plus the output of Agent 1 (so you have market phase, EMA slope, and trendline detection available).

## Functions to Implement

### 1. `detect_failed_final_flag(df, swings, entry_idx, is_long)`
Detect horizontal pause (ii pattern or small TTR) late in a trend that breaks out and reverses within 1-5 bars. Also detect: 1-2 bars after a climactic large trend bar. Returns `isFailedFinalFlag: boolean`.

### 2. `detect_functional_legs(df, swings, entry_idx, is_long)`
Analyze tails on pullback bars. A large tail functionally represents a second leg on a smaller timeframe. An apparent H1 where the tail dips significantly = Functional H2. Returns `isFunctionalLeg: boolean`, `functionalLegType: "H1→H2"|"L1→L2"|null`.

### 3. `detect_shrinking_stairs(df, swings, entry_idx)`
Identify 3+ trending swings in a broad channel. Measure breakout distance per swing. If distances are decreasing → shrinking stairs. Returns `isShrinkingStairs: boolean`, `stairDistances: number[]`.

### 4. `compute_pressure_score(df, entry_idx, lookback=20)`
Count consecutive trend bars, shaved bar frequency, close-at-extreme ratio in the lookback window. Returns `pressureScore: number (0-10)`, `consecutiveTrendBars: number`, `shavedBarFrequency: number`.

### 5. `detect_vacuum_magnet(df, entry_idx, entry_price, prior_day_high, prior_day_low, opening_gap, mm_target)`
Track distance to nearest price magnet (PDH, PDL, Gap, Measured Move). Returns `vacuumMagnet: "PDH"|"PDL"|"GAP"|"MM"|null`, `vacuumDistance: number`.

### 6. `detect_breakout_test(df, swings, entry_idx, entry_price)`
When a pullback tests the exact price of a prior breakout. Returns `isBreakoutTest: boolean`, `breakoutTestLevel: number|null`.

### 7. Enhanced `detect_gap_bar()` → `detect_gap_bar_with_2hm(df, ema_values, entry_idx, two_hm_minutes)`
Existing gap bar detection PLUS 2HM validation. Only "gold" if 2HM ≥ 120 min. Returns `gapBarWith2HM: boolean`.

### 8. `classify_shaved_bar_context(df, swings, entry_idx, is_shaved, is_long)`
If shaved bar is on Leg 1 → Urgency. If Leg 3+ → Exhaustion. Returns `shavedBarLeg: number`, `shavedBarIsExhaustion: boolean`.

### 9. `detect_always_in_status(df, ema_values, swings, entry_idx)`
Binary: who is in control based on last major breakout. Returns `alwaysInStatus: "LONG"|"SHORT"|"NEUTRAL"`.

### 10. `compute_prior_day_levels(all_candles, symbol, trade_date)`
Look up prior day's high/low and compute opening gap. Returns `priorDayHigh`, `priorDayLow`, `openingGap`.

### 11. `detect_spike_origin(df, ema_values, entry_idx, market_phase)`
If market phase is CHANNEL, identify the channel origin price (spike start). Returns `spikeOriginPrice: number|null`.

### 12. `detect_barb_wire_zones(df)`
Instead of per-bar detection, identify continuous ZONES of barb wire across the session. Returns array: `[{startIdx, endIdx, startTime, endTime}]` — stored in the sessions object.

## Integration Point
Wire all 12 functions into `analyze_trade()`. Store results in `flags` per the Schema Contract. Store session-level data (barb wire zones) in the sessions object.

## Output
Modified `fetch_and_analyze.py` with 12 new functions and updated `analyze_trade()`.

## Validation
- `python fetch_and_analyze.py` runs without errors
- Every new flag field from the Schema appears in the output JSON
- Functions are pure (no side effects, deterministic given same input)
- No new pip dependencies

---

# AGENT 3 — Scoring Engine

## Role
You **rewrite the Brooks Score computation** in `fetch_and_analyze.py`. The new scoring system uses the indicators from Agents 1+2, produces a `scoreDecomposition` with human-readable reasons, and classifies each trade.

## Your Context
You will receive: the **Schema Contract** from Agent 0, and you can assume all indicators from Agents 1+2 are available as flags on the trade object.

## New Scoring Weights
```
Total = Context × 25% + Setup × 25% + Signal × 20% + Institutional × 20% + Pressure × 10%

Context (base 5):
  +3 with-trend AND emaSlope is STEEP (confirmed trend)
  +3 with-trend when emaSlope is FLAT but entry is at range extreme (countertrend fade)
  -3 with-trend when emaSlope is FLAT and entry is mid-range (false trend signal)
  +3 2HM ≥ 120 min
  +2 stop run window (11:00-11:30 IST)
  -5 middle of middle
  Market phase modifier: SPIKE +2, CHANNEL +0, TRADING_RANGE -2 for breakout entries

Setup Reliability (base 5):
  +3 leg == 2 (second entry)
  +1 leg == 1 (first entry)
  +2 isFunctionalLeg (tail-based second entry)
  -2 leg >= 3 (overextended)
  +2 isFailedFinalFlag
  +2 failed failure detected
  -3 trendlineIntact AND trade is a reversal (cap at 5)
  -2 isShrinkingStairs AND with-trend entry (waning momentum)

Signal Bar Quality (base 5):
  +2 strong trend bar (body > 60%) — ONLY in SPIKE/CHANNEL phase
  +2 shaved bar on Leg 1 (urgency)
  -2 shaved bar on Leg 3+ (exhaustion trap)
  +1 doji on pullback in SPIKE phase (pause setup)
  -3 doji in barb wire in TRADING_RANGE
  -2 doji in TRADING_RANGE outside barb wire

Institutional (base 5):
  +2 institutional trap (entry in stop-hunt zone)
  +2 failed failure (specifically: Failed L2 in bull, Failed H2 in bear)
  +2 isBreakoutTest (pullback to prior breakout level)
  +1 vacuumMagnet is not null AND vacuumDistance > threshold (approaching magnet)
  -2 vacuumDistance < 5 ticks (entering at end of vacuum move)
  +2 alwaysInStatus matches trade direction
  -2 alwaysInStatus opposes trade direction

Pressure (base 5):
  Directly use pressureScore from Agent 2 (0-10 → scale to 1-10)

Special: If isStopRun AND isFailedFailure → auto-floor at 9/10
```

## Score Decomposition
For each component, collect `reasons: string[]` as you compute:
```python
reasons = []
if with_trend and ema_steep:
    context += 3
    reasons.append("+3: With-trend entry, EMA slope is STEEP (confirmed trend)")
```

## Trade Quality Classification
```python
# Based on score vs. P&L
if score.total >= 6 and pnl > 0:  tradeQuality = "GOOD_WIN"
if score.total >= 6 and pnl <= 0: tradeQuality = "GOOD_LOSS"
if score.total < 6 and pnl > 0:   tradeQuality = "BAD_WIN"
if score.total < 6 and pnl <= 0:  tradeQuality = "BAD_LOSS"
```

## Mistake Tag Generation
```python
if flags.isBarbWire:              mistakeTag = "#BARB_WIRE"
elif flags.isMiddleOfMiddle:      mistakeTag = "#LOW_CONTEXT"
elif flags.leg >= 3:              mistakeTag = "#OVEREXTENDED"
elif is_revenge(trade, all_trades, idx):  mistakeTag = "#REVENGE"  # loss then same-dir entry within 10min
elif score.total >= 8:            mistakeTag = "#PERFECT_STORM"
elif score.total >= 6:            mistakeTag = "#GOOD_ENTRY"
else:                             mistakeTag = "#FOMO"
```

## Thesis/Reality Auto-Generation
```python
# Thesis: derived from flags at entry
if flags.withTrend and flags.leg == 2:
    thesis = f"Second-entry {'bull' if is_long else 'bear'} pullback, expecting measured move to {mm_target:.0f}"
elif flags.isFailedFailure:
    thesis = "Liquidation engine: trapped traders fueling reversal"
# ... more patterns

# Reality: derived from actual outcome
if pnl > 0 and flags.reachedMM:
    reality = f"Measured move target reached — clean execution"
elif pnl < 0 and flags.isBarbWire:
    reality = "Entered Barb Wire zone — chopped out by overlapping bars"
# ... more patterns
```

## Output
Rewritten `compute_brooks_score()` + new helper functions for classification/tagging/thesis. All output fields match Schema Contract.

## Validation
- Score decomposition has reasons for every non-zero adjustment
- tradeQuality is set for every trade
- mistakeTag is set for every trade
- thesis and reality are non-empty strings for every trade
- profitLeftOnTable = MFE - abs(pnlPoints)
- maeAsStopPct = MAE / (signal_bar_range or entry_price * 0.001)

---

# AGENT 4 — Session Builder

## Role
You restructure the output of `fetch_and_analyze.py` to include the **shared sessions object** for the Trade Detail View. You also compute session-level metadata.

## Your Context
You will receive: the **Schema Contract** from Agent 0, plus the output of Agents 1-3 (all indicators and scoring are complete).

## Tasks

### 1. Build the `sessions` Object
In `run()`, after computing all per-trade analysis, build a top-level `sessions` dict:

```python
sessions = {}
for key, (df, ema_values, swings) in day_analysis.items():
    # key = "SYMBOL|DATE"
    sessions[key] = {
        "candles": [{"t": row.timestamp.strftime("%H:%M"), "o": row.open, "h": row.high, "l": row.low, "c": row.close} for _, row in df.iterrows()],
        "ema": ema_values,
        "swingPoints": [{"idx": s[0], "type": s[1], "price": s[2], "time": df.iloc[s[0]].timestamp.strftime("%H:%M")} for s in swings],
        "barbWireZones": detect_barb_wire_zones(df),  # from Agent 2
        "trendlines": compute_session_trendlines(df, swings),  # NEW
        "priorDayHigh": ...,
        "priorDayLow": ...,
        "openingGap": ...,
        "marketPhaseRegions": detect_market_phase_regions(df, ema_values),  # NEW
    }
```

### 2. Add `sessionKey` to Each Trade
Each trade gets `"sessionKey": "BANKNIFTY FEB FUT|2026-02-23"` referencing the sessions object.

### 3. Add Chart Index References per Trade
- `entryIdx`: index in session candles where entry occurred
- `exitIdx`: index where exit occurred
- `mfeBarIdx`: index of the candle with maximum favorable excursion
- `maeBarIdx`: index of the candle with maximum adverse excursion

### 4. Session-Level Functions to Implement
```python
def compute_session_trendlines(df, swings): ...
    # Connect swing lows for bull trendlines, swing highs for bear
    # Return: [{"type": "bull"|"bear", "startIdx": int, "endIdx": int, "startPrice": float, "endPrice": float, "broken": bool, "brokenAtIdx": int|null}]

def detect_market_phase_regions(df, ema_values): ...
    # Walk through the session and identify phase transitions
    # Return: [{"phase": "SPIKE"|"CHANNEL"|"TRADING_RANGE", "startIdx": int, "endIdx": int}]
```

### 5. Restructure Output Payload
```python
output_payload = {
    "generatedAt": ...,
    "broker": ...,
    "csvFile": ...,
    "dateRange": ...,
    "totalTrades": ...,
    "realDataTrades": ...,
    "sessions": sessions,      # NEW — shared chart data
    "trades": enriched,        # each trade now has sessionKey
}
```

## Output
Modified `run()` and new session-building functions in `fetch_and_analyze.py`.

## Validation
- `sessions` keys match all unique `sessionKey` values in trades
- Each session has candles array of length ~75
- Each session has ema array of same length as candles
- `entryIdx` and `exitIdx` are valid indices within the session's candle array
- No candle data is duplicated in per-trade objects (sparklineCandles can remain for backward compat)
- JSON file size is reasonable (~500KB-2MB, not 50MB)

---

# AGENT 5 — Card UI Agent

## Role
You upgrade the **trade card components** in `dashboard/src/App.jsx`. You do NOT build the detail view modal — that's Agent 6. You work on the overview layer: cards, summary header, heatmap, filters.

## Your Context
You will receive: the **Schema Contract** from Agent 0, plus the enriched_trades.json structure from Agent 4. You need to know what new fields are available in each trade's data.

## Existing Components to Modify

### 1. `TradeCard` — Add New Badges and Visual Treatments
- **Market Phase badge**: `[SPIKE]` (green/red), `[CHANNEL]` (blue), `[TRADING RANGE]` (gray) — prominent header position
- **Trade Quality badge**: 🏆 GOOD WIN / ✅ GOOD LOSS / ⚠️ BAD WIN / ❌ BAD LOSS — based on `tradeQuality` field
- **Mistake Tag badge**: `#FOMO`, `#LOW_CONTEXT`, etc. — small colored tag, based on `mistakeTag` field
- **Trendline badge**: "🔒 INTACT" or "🔓 BROKEN" — based on `trendlineIntact`
- **Always In Status**: "AIS: LONG ▲" or "AIS: SHORT ▼" — small badge
- **Enhanced Barb Wire Shield**: When `isBarbWire` is true, change the ENTIRE card border to muted purple with hatched pattern background. Not just a watermark — make it viscerally obvious. Goal: create an "ouch" reflex when scrolling.
- **Duration color-coding**: Green (<10min for scalps, <60min for swings), Amber (slightly long), Red (excessively long for setup type). Determine setup type from `marketPhase` + `leg`.
- **Prominent Leg Count**: Make the H1/H2/L1/L2 counter larger if `leg` was the primary entry signal (i.e., leg == 2).

### 2. `SummaryHeader` — Add Correlation Stats
- Add **Win Rate vs. Brooks Score** table/mini-chart:
  ```
  Score 1-3: XX% win rate (N trades)
  Score 4-5: XX% win rate (N trades)
  Score 6-7: XX% win rate (N trades)
  Score 8-10: XX% win rate (N trades)
  ```
- Add **"Top 3 Mistakes"** from `mistakeTag` frequency
- Add **"Profit Left on Table"** avg across all trades

### 3. `PainHeatmap` — Make Clickable
- Each daily P&L block becomes a **click target**
- Clicking a day sets the date filter to that specific date
- Visual feedback on click (border highlight, selected state)

### 4. `FilterBar` — Add Anti-Portfolio Toggle
- New button: **"🚫 Anti-Portfolio"** — when active:
  - Hides all wins
  - Shows only losses where `score.total < 6` OR `isBarbWire` OR `isMiddleOfMiddle`
  - Button has a red/danger visual style when active

### 5. `convertEnrichedToUIFormat()` — Handle New Fields
- Map all new `flags` fields from enriched JSON
- Map `tradeQuality`, `mistakeTag`, `thesis`, `reality`, `scoreDecomposition`
- Map `sessionKey` for detail view lookup
- Handle the `sessions` top-level object (store in state, pass to detail view)

## Output
Modified `App.jsx` with all card-level UI upgrades.

## Validation
- All new badges render without errors
- Barb Wire cards have visually distinct purple/hatched treatment
- Anti-Portfolio toggle correctly filters
- Clickable heatmap updates the date filter
- No new npm dependencies
- `npm run dev` shows no console errors

---

# AGENT 6 — Detail View Builder

## Role
You build the **Trade Detail Modal** — a click-to-expand full-session analysis view in `dashboard/src/App.jsx`. This is the primary learning tool: a full-day candlestick chart with Brooks annotations and analysis panels.

## Your Context
You will receive: the **Schema Contract** from Agent 0, the `sessions` data structure from Agent 4, and the card click handler from Agent 5.

## Components to Build

### 1. `TradeDetailModal` — Container
- Full-screen modal overlay (dark background, `z-50`)
- Close on Escape key or clicking backdrop
- Left/Right arrow keys to navigate between trades
- Responsive: stack chart above panels on narrow screens

### 2. `SessionCandlestickChart` — SVG Full-Day Chart
Render ~75 bars from `sessions[sessionKey].candles`:
- **Candlestick bodies**: green (close > open) / red (close < open), proper wicks
- **20 EMA line**: smooth curve from `sessions[sessionKey].ema`
- **Entry marker**: blue vertical line + arrow at `entryIdx`
- **Exit marker**: green/red vertical line + arrow at `exitIdx`
- **Trade window shade**: semi-transparent highlight between entry and exit
- **Context window highlight**: pale blue background tint on 10-20 bars before entry
- **MFE line**: horizontal dotted green at MFE price level, labeled
- **MAE line**: horizontal dotted red at MAE price level, labeled
- **Prior Day H/L**: horizontal dashed gray lines from `sessions[sessionKey].priorDayHigh/Low`
- **Measured Move target**: horizontal dotted line with "MM" label
- Build entirely with SVG (no charting libraries). Scale: ~12px per candle width, responsive height.

### 3. `BrooksAnnotationLayer` — Togglable Overlays
Each annotation type has a toggle checkbox:
- **Swing points**: Small △/▽ at `sessions[sessionKey].swingPoints`
- **Barb Wire zones**: Gray shaded rectangles from `barbWireZones`
- **Trendlines**: Diagonal SVG lines from `trendlines` array
- **Leg labels**: "L1", "L2", "L3" text at swing points
- **Market Phase bands**: Colored background bands (green=SPIKE, blue=CHANNEL, gray=RANGE) from `marketPhaseRegions`
- Default: annotations OFF (clean chart). User toggles ON as needed.

### 4. `ScoreDecompositionPanel` — Analysis Sidebar/Bottom
- **Market Phase**: Large badge with explanation
- **Always In Status**: "ALWAYS IN SHORT ▼" display
- **Leg Breakdown**: "This was H2 of a bull pullback" narrative from flags
- **Brooks Checklist Table**:
  | Metric | Score | Cue | Reason |
  |--------|-------|-----|--------|
  For each component in `scoreDecomposition`, show score, ✅/⚠️/❌ icon, and reasons list
- **Trade Quality Badge**: 🏆/✅/⚠️/❌ large display
- **Thesis vs. Reality**: Two text blocks from `thesis` and `reality` fields
- **MFE/MAE Gauge**: Horizontal bar — green section (MFE as % of target), red section (MAE as % of stop)
- **Pro Narrative**: The `analogy` field displayed as a styled quote block

### 5. `BarByBarReplay` — Optional Slider
- Range slider from 0 to candles.length
- As slider moves right, candles beyond the position are hidden
- At each position, compute and display: current EMA, current leg count, current market phase
- Play/Pause button for auto-advance (1 bar per 500ms)
- This is the highest-value learning tool — simulates "Reading Price Charts Bar by Bar"

## UX Guidelines
- Chart default: candles + EMA + entry/exit only. Keep it clean.
- All annotations togglable via checkboxes in a small control panel
- Click the chart to jump the replay slider to that position
- Keyboard: Left/Right = prev/next trade, Escape = close, Space = play/pause replay

## Output
New components in `App.jsx`: `TradeDetailModal`, `SessionCandlestickChart`, `BrooksAnnotationLayer`, `ScoreDecompositionPanel`, `BarByBarReplay`. Plus click handler on `TradeCard`.

## Validation
- Modal opens on trade card click
- Chart renders all ~75 bars correctly
- Entry/exit markers are at correct positions
- EMA line is smooth and follows the data
- Annotations toggle on/off without errors
- Replay slider hides future bars progressively
- Left/Right navigation works
- No new npm dependencies

---

# AGENT 7 — Anti-Mistake Learning System

## Role
You build the **learning and self-correction features** in `dashboard/src/App.jsx`. These are the "coach" components that turn the dashboard from a scorecard into a training tool.

## Your Context
You will receive: the **Schema Contract** from Agent 0, plus the trade data with `tradeQuality`, `mistakeTag`, `profitLeftOnTable`, and `maeAsStopPct` from Agents 3-4.

## Components to Build

### 1. `WinRateByScoreChart` — In SummaryHeader
- Small bar chart or table showing win rate per Brooks Score bracket
- Brackets: 1-3, 4-5, 6-7, 8-10
- Per bracket: win count, loss count, win rate %, avg P&L
- Color: green bars for high win rate, red for low
- **The Insight**: This transforms the Brooks score into a strict Trade/No-Trade filter. If 5/10 has 20% win rate but 8/10 has 80%, the data screams "stop taking 5/10 trades."

### 2. `TopMistakesPanel` — In SummaryHeader
- Aggregate `mistakeTag` across all trades
- Show top 3-5 most frequent mistakes, weighted by loss magnitude
- Format: "#LOW_CONTEXT — 8 trades, ₹12,400 total loss (65% of all losses)"
- Color-coded by severity

### 3. `ProfitDisciplineStats` — In SummaryHeader
- **Avg Profit Left on Table**: mean of `profitLeftOnTable` across all trades
- **Avg MAE as % of Stop**: mean of `maeAsStopPct` across winning trades
- **Entry Timing Grade**: If avg MAE on winners > 70% of stop → "⚠️ PREMATURE ENTRIES — you're predicting turns instead of waiting for signal bar close"
- **Exit Timing Grade**: If avg Profit Left on Table > 50% of avg MFE → "⚠️ PREMATURE EXITS — you're leaving meat on the bone"

### 4. `AntiPortfolioView` — Wall of Shame
- When the Anti-Portfolio filter is active (from Agent 5's FilterBar button):
  - Apply a red-tinted header: "🚫 ANTI-PORTFOLIO — Your Unforced Errors"
  - Below the header, show aggregate stats: total count, total ₹ lost, avg score
  - Cards below are filtered to only Bad Losses and Barb Wire entries
  - Add a "Revenge Pattern Detector": if 2+ consecutive losses on same symbol within 30 minutes → flag as "REVENGE SEQUENCE" with a connecting line between the cards

### 5. `TradeQualityMatrix` — Summary View
- 2×2 grid showing count and total P&L per quadrant:
  ```
  ┌──────────────┬──────────────┐
  │ 🏆 GOOD WIN  │ ⚠️ BAD WIN   │
  │ 15 trades    │ 3 trades     │
  │ +₹18,400     │ +₹1,200      │
  ├──────────────┼──────────────┤
  │ ✅ GOOD LOSS │ ❌ BAD LOSS   │
  │ 8 trades     │ 14 trades    │
  │ -₹9,600      │ -₹16,800     │
  └──────────────┴──────────────┘
  ```
- Clickable: clicking a quadrant filters trades to that quality level
- **The Insight**: If Bad Wins + Bad Losses > 50% of trades, the trader is fundamentally not following the system.

### 6. `DisciplineTimeline` — Day-Level Learning
- For each trading day, compute:
  - Number of trades taken
  - How many were after a loss (potential revenge)
  - Score trend within the day (did quality deteriorate after losses?)
  - Time between trades (bunching = emotional trading)
- Display as a small timeline chart in the Pain Heatmap area
- Flag days where "3+ trades within 30 min" occurred (bunching/revenge pattern)

## Output
New components in `App.jsx`: `WinRateByScoreChart`, `TopMistakesPanel`, `ProfitDisciplineStats`, `AntiPortfolioView`, `TradeQualityMatrix`, `DisciplineTimeline`.

## Validation
- Win rate brackets are mathematically correct
- Mistake tags aggregate correctly (sum of individual tags = total trades)
- Quality matrix counts sum to total trades
- Revenge detection correctly identifies consecutive same-symbol losses within 30min
- Anti-portfolio filter shows only qualifying trades
- No new npm dependencies

---

# APPENDIX — Reference Material for All Agents

## Al Brooks Price Action Core Principles (Carry with Domain-Specific Agents)

1. **"Close is usually close enough"** — Functional patterns matter more than textbook perfection
2. **Barb Wire = No Trade Zone** — 3+ overlapping bars with a doji. Market is a magnet pulling price to the middle.
3. **2HM Rule** — Price away from EMA for 2+ hours signals trend strength. First EMA Gap Bar after 2HM = high-probability fade.
4. **Failed Failures = Highest Alpha** — When a reliable setup fails by one tick, the opposite move is fueled by trapped traders liquidating.
5. **Trendline Break Required** — No reversal trade is valid without a prior demonstrated trendline break.
6. **Always In Status** — At any point, either bulls or bears are "always in." Trading against the Always In direction is countertrend.
7. **Shrinking Stairs = Waning Momentum** — Decreasing breakout distances signal an imminent two-legged reversal.
8. **Good Fill = Bad Trade** — If the market gives you a "bargain" entry in a strong trend, you're likely being trapped.
9. **Vacuum Effect** — Price is sucked toward magnets (prior day H/L, gaps, measured moves). Entries near a magnet are late.
10. **Market Phases**: SPIKE (every pullback is a buy/sell), CHANNEL (trade at EMA), TRADING RANGE (fade failed breakouts only).

## File Paths
- Python backend: `fetch_and_analyze.py` (root directory)
- React frontend: `dashboard/src/App.jsx`
- Candle cache: `candle_cache/*.json`
- Output: `dashboard/public/enriched_trades.json`
- History: `analysis_history/analysis_*.json`

## Constraints (Apply to ALL Agents)
- Python: `numpy`, `pandas`, standard library only — NO new pip dependencies
- React: Tailwind CSS for styling — NO additional npm packages, NO charting libraries
- Candlestick chart: SVG or Canvas only
- All timestamps: IST (UTC+5:30)
- 5-minute candle timeframe
- EMA period: 20 (standard Brooks)
- NSE market hours: 9:15–15:30 IST
