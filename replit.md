# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Structure

```text
artifacts-monorepo/
├── artifacts/              # Deployable applications
│   └── api-server/         # Express API server
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts (single workspace package)
│   └── src/                # Individual .ts scripts, run via `pnpm --filter @workspace/scripts run <script>`
├── pnpm-workspace.yaml     # pnpm workspace (artifacts/*, lib/*, lib/integrations/*, scripts)
├── tsconfig.base.json      # Shared TS options (composite, bundler resolution, es2022)
├── tsconfig.json           # Root TS project references
└── package.json            # Root package with hoisted devDeps
```

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references. This means:

- **Always typecheck from the root** — run `pnpm run typecheck` (which runs `tsc --build --emitDeclarationOnly`). This builds the full dependency graph so that cross-package imports resolve correctly. Running `tsc` inside a single package will fail if its dependencies haven't been built yet.
- **`emitDeclarationOnly`** — we only emit `.d.ts` files during typecheck; actual JS bundling is handled by esbuild/tsx/vite...etc, not `tsc`.
- **Project references** — when package A depends on package B, A's `tsconfig.json` must list B in its `references` array. `tsc --build` uses this to determine build order and skip up-to-date packages.

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages that define it
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Trading Bot — Modular Architecture

Python crypto trading bot running on Bitget demo mode. **Refactored into 3 modules** (May 2026).

### Module layout
| File | Purpose |
|------|---------|
| `config.py` | All static constants, file paths, env-loaded values. Import everywhere with `from config import *`. |
| `market_logic.py` | Pure strategy engine: `score_symbol`, `detect_fvg`, `detect_order_blocks` (ICT OB, NEW), `detect_flag`, `detect_bb_squeeze`, `detect_volume_buildup`, `detect_rsi_divergence`, `get_fear_greed`, `get_fng_mode`, `calc_risk_position`, `get_dynamic_sl`. |
| `bot.py` | Orchestration: scan loop, trade management, Telegram handlers, Flask API, async pre-fetch. |
| `keep_alive.py` | Flask app — unchanged helper. |
| `gdrive_reporter.py` | Google Drive audit reporter — unchanged helper. |

### Key Constants (now in config.py)
- `MIN_SCORE=78`, `MAX_TRADES=3` (dynamic, loaded from config.json), `LEVERAGE=10`, `MARGIN=50`
- `VERBOSE_LOG=False` — set to `True` for per-symbol scoring breakdown (debug only)
- `SCALP_SCAN_INTERVAL=300` (5 min active), `SCALP_SCAN_INTERVAL_WAIT=600` (10 min WAIT mode)

### Async OHLCV Pre-fetch
`_scan_batch()` now calls `asyncio.run(_prefetch_ohlcv(candidates))` before the scoring loop, fetching 4H+1H data for all candidates in parallel via `ccxt.async_support.bitget`. Results cached in `_ohlcv_cache`; `get_data_cached()` serves cache hits transparently.

### Order Block Detection (ICT)
`detect_order_blocks(df, direction)` in `market_logic.py`: finds the last bearish candle before a 3-candle bullish impulse (Bullish OB) or last bullish candle before a 3-candle bearish impulse (Bearish OB). Scores +3 points if current price is inside or within 1% of the zone.

### Threads
| Thread | Interval | Notes |
|--------|----------|-------|
| trade_monitor_loop | 60s | SL/TP/BE/Trailing — only runs when active_trades exist |
| scan_loop | 60 min | Auto-scan (Production only, disabled in dev) |
| sol_watch_loop | 15 min | SOL/USDT watch, alerts on state change only |
| scalp_scan_loop | 5 min active / 10 min WAIT | Runs only when FNG < 13 (Extreme Fear) |
| watch_loop | 15 min | User Watch-List |

### Low-Resource Mode
- `VERBOSE_LOG=False`: Only Entry/Exit/Error events printed. No per-symbol scoring noise.
- FNG API cached 1h; only prints when value changes.
- `sentiment_check()` only prints when regime changes (Kill-Switch → Neutral etc.)
- Scalp scanner doubles sleep to 10 min when not in Extreme Fear (normal market).
- `save_active_trades()` / `save_wallet()` only called on trade events — not on every poll.

### Wallet API (`/api/wallet`)
Returns: `balance`, `available_balance`, `locked_balance`, `unrealized_pnl`, `equity`, `active_count`, `total_pnl`, `equity_history`.

### Flask Port
`8091` (internal), proxied via API Server at port 8080.

## Packages

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/` and use `@workspace/api-zod` for request and response validation and `@workspace/db` for persistence.

- Entry: `src/index.ts` — reads `PORT`, starts Express
- App setup: `src/app.ts` — mounts CORS, JSON/urlencoded parsing, routes at `/api`
- Routes: `src/routes/index.ts` mounts sub-routers; `src/routes/health.ts` exposes `GET /health` (full path: `/api/health`)
- Depends on: `@workspace/db`, `@workspace/api-zod`
- `pnpm --filter @workspace/api-server run dev` — run the dev server
- `pnpm --filter @workspace/api-server run build` — production esbuild bundle (`dist/index.cjs`)
- Build bundles an allowlist of deps (express, cors, pg, drizzle-orm, zod, etc.) and externalizes the rest

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL. Exports a Drizzle client instance and schema models.

- `src/index.ts` — creates a `Pool` + Drizzle instance, exports schema
- `src/schema/index.ts` — barrel re-export of all models
- `src/schema/<modelname>.ts` — table definitions with `drizzle-zod` insert schemas (no models definitions exist right now)
- `drizzle.config.ts` — Drizzle Kit config (requires `DATABASE_URL`, automatically provided by Replit)
- Exports: `.` (pool, db, schema), `./schema` (schema only)

Production migrations are handled by Replit when publishing. In development, we just use `pnpm --filter @workspace/db run push`, and we fallback to `pnpm --filter @workspace/db run push-force`.

### `lib/api-spec` (`@workspace/api-spec`)

Owns the OpenAPI 3.1 spec (`openapi.yaml`) and the Orval config (`orval.config.ts`). Running codegen produces output into two sibling packages:

1. `lib/api-client-react/src/generated/` — React Query hooks + fetch client
2. `lib/api-zod/src/generated/` — Zod schemas

Run codegen: `pnpm --filter @workspace/api-spec run codegen`

### `lib/api-zod` (`@workspace/api-zod`)

Generated Zod schemas from the OpenAPI spec (e.g. `HealthCheckResponse`). Used by `api-server` for response validation.

### `lib/api-client-react` (`@workspace/api-client-react`)

Generated React Query hooks and fetch client from the OpenAPI spec (e.g. `useHealthCheck`, `healthCheck`).

### `scripts` (`@workspace/scripts`)

Utility scripts package. Each script is a `.ts` file in `src/` with a corresponding npm script in `package.json`. Run scripts via `pnpm --filter @workspace/scripts run <script>`. Scripts can import any workspace package (e.g., `@workspace/db`) by adding it as a dependency in `scripts/package.json`.
