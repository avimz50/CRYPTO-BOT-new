# Crypto Trading Bot — Master Control Dashboard

Python "Adaptive Sniper" crypto trading bot on Bitget (virtual/demo), with a React/TypeScript dashboard and Express API proxy.

## Run & Operate

| Command | Purpose |
|---------|---------|
| `python bot.py` | Start the trading bot (port 8091) |
| `pnpm --filter @workspace/api-server run dev` | Express API proxy (port 8080) |
| `pnpm --filter @workspace/bot-dashboard run dev` | React dashboard (port from $PORT) |
| `pnpm run typecheck` | Full workspace typecheck |
| `pnpm --filter @workspace/db run push` | Apply DB migrations |

**Required env vars**: `BITGET_KEY`, `BITGET_SECRET`, `BITGET_PW`, `TELEGRAM_TOKEN`, `CHAT_ID`, `GDRIVE_FOLDER_ID`, `GDRIVE_SERVICE_ACCOUNT_JSON`, `BOT_URL` (Flask URL for API proxy)

## Stack

- **Bot**: Python 3, ccxt (Bitget), Flask (keep_alive.py), asyncio
- **API server**: Express 5, TypeScript, Fastify-style logging (pino)
- **Dashboard**: React 19, Vite 7, Tailwind CSS 4, TypeScript
- **Monorepo**: pnpm workspaces, Node 24, TypeScript 5.9

## Where things live

```
artifacts/
  bot-dashboard/src/
    App.tsx                    ← main dashboard orchestrator
    hooks/useBotData.ts        ← all polling hooks + save helpers
    components/
      Header.tsx               ← header + sync button
      Sidebar.tsx              ← nav sidebar
      FinancialOverview.tsx    ← equity/P&L panel + sparkline
      ActiveTradesTable.tsx    ← live trades table
      ScanStatus.tsx           ← last scan results table
      BotSettings.tsx          ← max trades + amount + leverage sliders
      TradeHistoryModal.tsx    ← full trade history modal
      LogsView.tsx             ← stdout/stderr/crash log viewer
      FngGauge.tsx             ← SVG fear & greed gauge
      Sparkline.tsx            ← SVG equity sparkline
  api-server/src/
    routes/bot.ts              ← proxies all /api/* → Flask :8091
bot.py                         ← main bot (DO NOT EDIT without task)
config.py                      ← all constants
market_logic.py                ← pure strategy engine
keep_alive.py                  ← Flask API (port 8091)
gdrive_reporter.py             ← Google Drive audit reporter
config.json                    ← runtime config (max_trades, leverage etc.)
active_trades.json             ← live trade state
wallet.json                    ← wallet + equity_history
audit_report.json              ← closed trade history
```

## Architecture decisions

- **Vite proxy**: `/api/*` → `http://localhost:8080` (Express) → `$BOT_URL` (Flask bot)
- **Polling not WebSocket**: dashboard polls every 10s (status/trades), 30s (scan/slots/wallet), 5s (logs when on Logs tab), 60s (audit when modal open)
- **No per-trade leverage in bot data**: leverage column in Trade History renders `audit.leverage` if present, otherwise "—"
- **Bot Settings persistence**: `max_trades` → POST `/api/slots`; F&G thresholds + `amount_per_trade` + `default_leverage` → POST `/api/fng_settings` (Flask handles thresholds; amount/leverage fields forwarded but not yet stored server-side by Flask); `amount_per_trade`/`default_leverage` also written to `localStorage` as client-side cache so sliders restore on next load
- **Tailwind + inline styles**: panel/card backgrounds use inline CSS vars for the `#070d1a → #0a1628 → #0d1f3c` dark blue palette; Tailwind handles spacing/layout

## Product

- **Dashboard tab**: Financial overview (equity, P&L, floating/realized, equity sparkline), active trades table with live P&L, scan results table, Fear & Greed gauge, bot settings sliders, quick stats
- **Logs tab**: Live bot stdout/stderr + crash banner, auto-scrolls, copy button, refreshes every 5s
- **Settings tab**: Same BotSettings panel as sidebar for standalone access
- **Trade History modal**: Full closed trade audit with win rate, total P&L, sortable table
- **Header**: Sync with Telegram button, online/offline indicator, live BTC price, clock

## User preferences

- המשתמש: אבי, זכר
- Dark blue palette: `#070d1a` base, `#0a1628` panels, `#0d1f3c` cards, `#1e3a5f` borders, `#3b82f6` accent
- Compact, data-dense UI with monospace numbers
- Hebrew locale for clock (user originally requested — now using system locale)

## Gotchas

- **bot.py must NOT be modified** unless explicitly in a task — it is the live trading engine
- Bot Flask runs on port 8091; API server on 8080; dashboard on $PORT (assigned by Replit)
- `active_trades.json` / `wallet.json` / `audit_report.json` are written by the bot and read-only from the dashboard perspective
- `BOT_URL` env var must point to the production bot URL so the dev dashboard can proxy to it

## Pointers

- Bot strategy: `market_logic.py` → `score_symbol()`, `detect_order_blocks()`, `detect_flag()`
- API routes: `artifacts/api-server/src/routes/bot.ts`
- Dashboard mockup (reference): `artifacts/mockup-sandbox/src/components/mockups/master-control/MasterControl.tsx`
- pnpm workspace skill: `.local/skills/pnpm-workspace/SKILL.md`
