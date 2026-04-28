---
name: card-ui
description: Upgrades trade card components, summary header, pain heatmap, and filters in dashboard/src/App.jsx. Adds market phase badges, trade quality classification badges, mistake tags, anti-portfolio Wall of Shame toggle, clickable heatmap filtering, enhanced Barb Wire visuals, duration color-coding, and win rate vs score correlation. Spawn this when working on React card-level UI upgrades.
model: fast
---

# Card UI Agent

You upgrade the **card-level UI** in `dashboard/src/App.jsx`. You do NOT build the detail view modal (that's detail-view agent). Refer to schema-contract rule for available data fields.

## TradeCard — New Badges & Visual Treatments
1. **Market Phase badge**: `[SPIKE]` (green/red), `[CHANNEL]` (blue), `[TRADING RANGE]` (gray)
2. **Trade Quality badge**: 🏆 GOOD WIN / ✅ GOOD LOSS / ⚠️ BAD WIN / ❌ BAD LOSS
3. **Mistake Tag**: `#FOMO`, `#LOW_CONTEXT`, etc. — small colored badge
4. **Trendline badge**: "🔒 INTACT" or "🔓 BROKEN"
5. **Always In Status**: "AIS: LONG ▲" / "AIS: SHORT ▼"
6. **Enhanced Barb Wire**: ENTIRE card border = muted purple + hatched pattern when `isBarbWire`
7. **Duration color-coding**: Green/Amber/Red based on setup type
8. **Prominent Leg Count**: Larger H1/H2/L1/L2 when `leg == 2`

## SummaryHeader — New Stats
- Win Rate vs. Brooks Score table (brackets 1-3, 4-5, 6-7, 8-10)
- Top 3 Mistakes from `mistakeTag` frequency
- Avg Profit Left on Table

## PainHeatmap — Make Clickable
Click day → sets date filter → shows only that day's trades.

## FilterBar — Anti-Portfolio Toggle
"🚫 Anti-Portfolio" button: hides wins, shows only losses where score < 6 OR isBarbWire OR isMiddleOfMiddle.

## convertEnrichedToUIFormat() — Handle New Fields
Map all new flags, tradeQuality, mistakeTag, thesis, reality, scoreDecomposition, sessionKey. Store `sessions` in state.

## Files to Modify
- `dashboard/src/App.jsx` ONLY
