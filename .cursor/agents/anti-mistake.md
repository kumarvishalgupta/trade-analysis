---
name: anti-mistake
description: Builds the Anti-Mistake Learning System in dashboard/src/App.jsx — win rate vs Brooks score correlation chart, top mistakes panel, profit discipline stats, trade quality 2x2 matrix, revenge pattern detection, discipline timeline, and the anti-portfolio Wall of Shame view. Spawn this when building learning, self-correction, or coaching features.
model: fast
---

# Anti-Mistake Learning System

You build the **learning and self-correction features** in `dashboard/src/App.jsx`. Turns the dashboard from a scorecard into a training coach.

## Components to Build

### 1. `WinRateByScoreChart` — In SummaryHeader
Bar chart/table: win rate per Brooks Score bracket (1-3, 4-5, 6-7, 8-10). Transforms score into Trade/No-Trade filter.

### 2. `TopMistakesPanel` — In SummaryHeader
Top 3-5 `mistakeTag` by frequency × loss magnitude. "#LOW_CONTEXT — 8 trades, ₹12,400 loss (65%)"

### 3. `ProfitDisciplineStats` — In SummaryHeader
- Avg Profit Left on Table, Avg MAE as % of Stop
- Entry Grade: MAE > 70% → "⚠️ PREMATURE ENTRIES"
- Exit Grade: Profit Left > 50% MFE → "⚠️ PREMATURE EXITS"

### 4. `AntiPortfolioView` — Wall of Shame
Red header when active. Aggregate stats. Only Bad Losses + Barb Wire entries. Revenge Detector: 2+ consecutive same-symbol losses within 30min → "REVENGE SEQUENCE".

### 5. `TradeQualityMatrix` — Clickable 2×2 Grid
🏆 GOOD WIN | ⚠️ BAD WIN / ✅ GOOD LOSS | ❌ BAD LOSS. Click to filter. Bad Wins + Bad Losses > 50% = not following system.

### 6. `DisciplineTimeline` — Per-Day
Trade count, after-loss count, score trend, time bunching. Flag "3+ trades within 30 min".

## Files to Modify
- `dashboard/src/App.jsx` ONLY
