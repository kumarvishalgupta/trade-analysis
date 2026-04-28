
# Scoring Engine

You **rewrite the Brooks Score** in `fetch_and_analyze.py`. Uses indicators from core-logic-fixer and indicator-builder. Produces `scoreDecomposition` with reasons, classifies trade quality, generates mistake tags and thesis/reality.

## New Scoring Weights
```
Total = Context × 25% + Setup × 25% + Signal × 20% + Institutional × 20% + Pressure × 10%

Context (base 5):
  +3 with-trend AND emaSlope STEEP
  +3 with-trend when FLAT but at range extreme (countertrend fade)
  -3 with-trend when FLAT and mid-range (false signal)
  +3 2HM ≥ 120 min, +2 stop run window, -5 middle of middle
  Market phase: SPIKE +2, CHANNEL +0, TRADING_RANGE -2 for breakouts

Setup (base 5):
  +3 leg == 2, +1 leg == 1, +2 isFunctionalLeg, -2 leg >= 3
  +2 isFailedFinalFlag, +2 failed failure
  -3 trendlineIntact AND reversal (cap at 5), -2 isShrinkingStairs AND with-trend

Signal (base 5):
  +2 strong trend bar (SPIKE/CHANNEL only)
  +2 shaved Leg 1 (urgency), -2 shaved Leg 3+ (exhaustion)
  +1 doji on pullback in SPIKE, -3 doji in barb wire TR, -2 doji in TR

Institutional (base 5):
  +2 trap, +2 failed failure (Failed L2/H2), +2 breakout test
  +1 vacuumMagnet not null AND distance > threshold, -2 vacuumDistance < 5 ticks
  +2 AIS matches direction, -2 AIS opposes

Pressure (base 5): use pressureScore directly (0-10 → 1-10)
SPECIAL: isStopRun AND isFailedFailure → auto-floor at 9/10
```

## Score Decomposition
Collect `reasons: string[]` for every non-zero adjustment.

## Trade Quality Classification
- score >= 6 AND pnl > 0 → "GOOD_WIN"
- score >= 6 AND pnl <= 0 → "GOOD_LOSS"
- score < 6 AND pnl > 0 → "BAD_WIN"
- score < 6 AND pnl <= 0 → "BAD_LOSS"

## Mistake Tag: "#BARB_WIRE", "#LOW_CONTEXT", "#OVEREXTENDED", "#REVENGE", "#PERFECT_STORM", "#GOOD_ENTRY", "#FOMO"

## Thesis/Reality: Auto-generate from flags + outcome. profitLeftOnTable = MFE - abs(pnlPoints). maeAsStopPct = MAE / (signal_bar_range or entry_price * 0.001)

## Files to Modify
- `fetch_and_analyze.py` ONLY
