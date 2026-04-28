---
name: detail-view
description: Builds the Trade Detail Modal in dashboard/src/App.jsx — a click-to-expand full-session candlestick chart (SVG) with Brooks annotations, score decomposition panel, MFE/MAE Target-vs-Heat gauge, thesis/reality gap analysis, trade quality badge, and bar-by-bar replay slider. Spawn this when building the trade detail drill-down view or candlestick chart.
model: fast
---

# Detail View Builder

You build the **Trade Detail Modal** in `dashboard/src/App.jsx`. Full-day candlestick chart + Brooks annotations + analysis panels. Data source: `sessions[trade.sessionKey]`.

## Components to Build

### 1. `TradeDetailModal` — Container
Full-screen modal (dark bg, z-50). Escape/backdrop to close. Left/Right = prev/next trade. Responsive.

### 2. `SessionCandlestickChart` — SVG Full-Day Chart (~75 bars)
- Candlestick bodies (green/red) + wicks
- 20 EMA line from `session.ema`
- Entry marker (blue) at `entryIdx`, Exit marker (green/red) at `exitIdx`
- Trade window shade between entry/exit
- Context window highlight (pale blue on 10-20 bars before entry)
- MFE/MAE horizontal dotted lines
- Prior Day H/L dashed gray lines
- Measured Move target dotted line
- **SVG only** — no charting libraries. ~12px per candle.

### 3. `BrooksAnnotationLayer` — Togglable (default OFF)
- Swing points △/▽, Barb Wire gray zones, Trendlines, Leg labels, Market Phase bands

### 4. `ScoreDecompositionPanel`
- Market Phase badge, Always In Status, Leg Breakdown narrative
- Brooks Checklist Table (score + ✅/⚠️/❌ + reasons per component)
- Trade Quality Badge (🏆/✅/⚠️/❌)
- Thesis vs. Reality text blocks
- MFE/MAE Gauge (green=MFE%, red=MAE%)
- Pro Narrative (analogy quote)

### 5. `BarByBarReplay` — Slider
Range slider hides future candles. Shows current EMA/leg/phase. Play/Pause (500ms). Space = play/pause.

## Files to Modify
- `dashboard/src/App.jsx` ONLY
