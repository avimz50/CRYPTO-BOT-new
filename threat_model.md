# Threat Model

## Project Overview

This project is a publicly deployed crypto trading dashboard and bot controller. The production stack consists of a React/Vite dashboard, an Express API server exposed to the internet, and a Python trading bot with a Flask API that the Express layer proxies to. The bot interacts with Bitget demo trading, Telegram, Google Drive, external market-data APIs, and Replit Object Storage.

The deployment is public, so every internet-facing HTTP endpoint in the Express server must be treated as reachable by unauthenticated attackers. The mockup sandbox is a development-only artifact and is out of scope unless production reachability is later demonstrated.

## Assets

- **Trading bot control plane** — endpoints and commands that can change strategy thresholds, trade capacity, or trigger trading actions. Unauthorized access can manipulate bot behavior or close/open positions.
- **Operational trading data** — wallet balance, realized/unrealized P&L, open positions, trade history, scan results, and watchlists. This is sensitive business and financial telemetry.
- **Bot-integrated identities and secrets** — Bitget API credentials, Telegram bot token, Google Drive credentials, Gemini credentials, and any webhook secrets. Exposure could enable third-party account abuse.
- **Persistent state** — active trades, wallet snapshots, audit logs, and object-storage state that survive restarts. Tampering can corrupt trading logic and operator visibility.
- **Bot logs and crash artifacts** — stdout, stderr, crash traces, and restart metadata. These can reveal internal paths, runtime behavior, and occasionally sensitive payload fragments.

## Trust Boundaries

- **Browser / Internet to Express API** — the `/api/*` routes are the primary public boundary. The client is untrusted and every sensitive action must be authenticated and authorized server-side.
- **Express API to Python Flask bot** — the Node proxy can forward unauthenticated public requests into the internal bot control surface. Any weak or missing validation here exposes bot internals.
- **Bot to external services** — the bot calls Bitget, Telegram, Google Drive, Gemini, CoinGecko, Kraken, and Alternative.me using secrets or trust in third-party responses.
- **Bot to Object Storage / disk cache** — persistent state crosses from application memory into durable storage and local files. Attackers who reach state-mutating endpoints can influence what is stored and later served.
- **Authenticated operator to everyone else** — the intended operator controls the bot via Telegram/dashboard, but the current architecture includes public-facing routes that can blur this boundary if not explicitly enforced.
- **Production vs dev-only artifacts** — `artifacts/mockup-sandbox/` is assumed non-production and should usually be ignored; `artifacts/api-server/`, `artifacts/bot-dashboard/`, `bot.py`, `keep_alive.py`, `watchdog.py`, and persistence files are production-relevant.

## Scan Anchors

- **Production entry points:** `scripts/start_production.sh`, `artifacts/api-server/src/index.ts`, `artifacts/api-server/src/app.ts`, `artifacts/api-server/src/routes/`, `bot.py`, `keep_alive.py`.
- **Highest-risk code areas:** `artifacts/api-server/src/routes/bot.ts` (public proxy), `bot.py` Flask routes and Telegram handlers, log-serving endpoints, object-storage fallback reads.
- **Public surfaces:** Express `/api/*` endpoints including `/api/tg_hook`, `/api/slots`, `/api/fng_settings`, `/api/sync`, `/api/bot_log`, `/api/debug`, and data reads such as `/api/wallet` and `/api/trades`.
- **Dev-only surfaces to usually ignore:** `artifacts/mockup-sandbox/` unless production exposure is demonstrated.

## Threat Categories

### Spoofing

The bot accepts operator commands through public HTTP routes and Telegram updates. The system must ensure only the legitimate operator or trusted upstream services can trigger privileged actions. Telegram webhook traffic and any webhook-style endpoint must be authenticated in a way attackers cannot forge.

### Tampering

Public requests can reach endpoints that mutate live runtime configuration and trading state. The application must ensure that all state-changing routes require strong server-side authorization and that bot commands cannot be injected through untrusted HTTP payloads.

### Information Disclosure

The dashboard and API expose financial telemetry, open positions, trade history, logs, and debug information. The system must ensure that sensitive operational data is not readable by arbitrary internet users and that logs/debug responses never expose secrets, internal paths, or unnecessary runtime details.

### Denial of Service

Several public endpoints trigger network calls, scans, Telegram actions, or log processing. The system must ensure attackers cannot repeatedly invoke expensive operations or create retry storms that degrade the bot or spam external integrations.

### Elevation of Privilege

The most important privilege boundary is between the single intended operator and the public internet. The system must ensure public dashboard users cannot escalate into bot-controller privileges, modify strategy parameters, issue trading commands, or invoke internal maintenance paths through the Express proxy or the Telegram webhook flow.