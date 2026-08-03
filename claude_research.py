"""
claude_research.py — Dr. Sniper Autonomous Market Research

Claude independently scans the market every 2 hours using multi-source data:
  1. DISCOVER  — CoinGecko trending + top gainers/losers
  2. ANALYZE   — TA (RSI/EMA from exchange), DeFiLlama TVL, web search
  3. DECIDE    — Conviction score, then execute or alert

Execution thresholds:
  score ≥ 78 + execute=true  →  auto-execute via callback (if enabled)
  score 65-77                →  Telegram alert only
  score < 65                 →  silent

Callbacks registered from bot.py (after function definitions):
  set_exchange(ex)
  set_execute_trade_fn(fn)     fn(symbol, direction, claude_score, reason, key_risk)
  set_send_msg_fn(fn)          fn(text)
  set_context_fns(get_open_trades, get_daily_pnl, get_fng)
"""

import os
import json
import time
import threading
import requests
from collections import deque
import anthropic


# ── Injected dependencies ─────────────────────────────────────────────────────
_exchange:           object = None
_execute_trade_fn          = None   # (symbol, direction, score, reason, key_risk)
_send_msg_fn               = None   # (text: str)
_get_open_trades_fn        = None   # () → int
_get_daily_pnl_fn          = None   # () → float
_get_fng_fn                = None   # () → int


def set_exchange(ex) -> None:
    global _exchange
    _exchange = ex
    print("[Research] ✅ Exchange registered", flush=True)


def set_execute_trade_fn(fn) -> None:
    global _execute_trade_fn
    _execute_trade_fn = fn


def set_send_msg_fn(fn) -> None:
    global _send_msg_fn
    _send_msg_fn = fn


def set_context_fns(get_open_trades, get_daily_pnl, get_fng) -> None:
    global _get_open_trades_fn, _get_daily_pnl_fn, _get_fng_fn
    _get_open_trades_fn = get_open_trades
    _get_daily_pnl_fn   = get_daily_pnl
    _get_fng_fn         = get_fng


# ── Auto-execute toggle ───────────────────────────────────────────────────────
_auto_execute = True

def set_auto_execute(enabled: bool) -> None:
    global _auto_execute
    _auto_execute = enabled

def is_auto_execute() -> bool:
    return _auto_execute


# ── Research state ────────────────────────────────────────────────────────────
_research_lock = threading.Lock()

research_stats: dict = {
    'runs':             0,
    'candidates_found': 0,
    'trades_executed':  0,
    'alerts_sent':      0,
    'last_run':         None,
    'last_candidates':  deque(maxlen=5),
}


# ── Singleton Anthropic client ────────────────────────────────────────────────
_client: anthropic.Anthropic | None = None
_client_init_failed = False

def _get_client() -> anthropic.Anthropic | None:
    global _client, _client_init_failed
    if _client_init_failed:
        return None
    if _client is not None:
        return _client
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        print("[Research] ⚠️  ANTHROPIC_API_KEY missing", flush=True)
        _client_init_failed = True
        return None
    try:
        _client = anthropic.Anthropic(api_key=api_key)
        print("[Research] ✅ Anthropic client ready", flush=True)
    except Exception as e:
        print(f"[Research] ❌ client init: {e}", flush=True)
        _client_init_failed = True
    return _client


# ── CoinGecko helpers ─────────────────────────────────────────────────────────
_CG_HEADERS = {'User-Agent': 'AdaptiveSniper/1.0'}


def _cg_get(path: str, params: dict | None = None) -> dict | list:
    r = requests.get(
        f"https://api.coingecko.com/api/v3{path}",
        headers=_CG_HEADERS,
        params=params or {},
        timeout=8,
    )
    return r.json()


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_get_trending_coins() -> str:
    """CoinGecko: top 7 trending coins right now."""
    try:
        data  = _cg_get("/search/trending")
        coins = data.get('coins', [])[:7]
        lines = []
        for c in coins:
            item = c.get('item', {})
            lines.append(
                f"{item.get('symbol','?').upper()}/USDT  "
                f"rank #{item.get('market_cap_rank','?')}  "
                f"id:{item.get('id','?')}"
            )
        return "\n".join(lines) if lines else "No trending data available."
    except Exception as e:
        return f"CoinGecko trending error: {e}"


def _tool_get_top_movers(direction: str = 'gainers') -> str:
    """CoinGecko: top 12 gainers or losers over 24h."""
    order = ('price_change_percentage_24h_desc' if direction == 'gainers'
             else 'price_change_percentage_24h_asc')
    try:
        data  = _cg_get("/coins/markets", {
            'vs_currency': 'usd', 'order': order,
            'per_page': 15, 'page': 1,
            'price_change_percentage': '24h',
            'sparkline': 'false',
        })
        lines = []
        for c in data[:12]:
            pct  = c.get('price_change_percentage_24h') or 0
            vol  = (c.get('total_volume') or 0) / 1_000_000
            mcap = (c.get('market_cap')   or 0) / 1_000_000_000
            lines.append(
                f"{c.get('symbol','?').upper()}/USDT  "
                f"${c.get('current_price',0):,.4g}  "
                f"{pct:+.1f}% 24h  "
                f"vol ${vol:.0f}M  mcap ${mcap:.1f}B  "
                f"id:{c.get('id','?')}"
            )
        return "\n".join(lines) if lines else "No data."
    except Exception as e:
        return f"CoinGecko movers error: {e}"


def _tool_get_coin_data(coin_id: str) -> str:
    """CoinGecko: full market profile for a coin."""
    try:
        d  = _cg_get(f"/coins/{coin_id}", {
            'localization': 'false', 'tickers': 'false',
            'market_data': 'true', 'community_data': 'true',
            'developer_data': 'false',
        })
        md  = d.get('market_data', {})
        usd = lambda k: (md.get(k) or {}).get('usd', 'N/A')
        pct = lambda k: md.get(k, 'N/A')
        vol = (usd('total_volume') or 0)
        vol_txt = f"${vol/1e6:.1f}M" if isinstance(vol, (int, float)) else vol
        mcap = usd('market_cap')
        mcap_txt = f"${mcap/1e9:.2f}B" if isinstance(mcap, (int, float)) else mcap
        sent  = d.get('sentiment_votes_up_percentage', 'N/A')
        cats  = ", ".join((d.get('categories') or [])[:3])
        lines = [
            f"Name: {d.get('name')} ({d.get('symbol','').upper()}/USDT)",
            f"Price: ${usd('current_price')}",
            f"Changes: 24h={pct('price_change_percentage_24h')}%  7d={pct('price_change_percentage_7d')}%  30d={pct('price_change_percentage_30d')}%",
            f"Market cap: {mcap_txt}  rank #{d.get('market_cap_rank','N/A')}",
            f"24h volume: {vol_txt}",
            f"ATH distance: {pct('ath_change_percentage')}%",
            f"Community: {sent}% bullish",
        ]
        if cats:
            lines.append(f"Categories: {cats}")
        return "\n".join(lines)
    except Exception as e:
        return f"CoinGecko coin data error '{coin_id}': {e}"


def _tool_get_defi_tvl(symbol: str) -> str:
    """DeFiLlama: TVL data for DeFi protocols."""
    try:
        clean     = symbol.replace('/USDT', '').upper()
        protocols = requests.get("https://api.llama.fi/protocols", timeout=8).json()
        matches   = [p for p in protocols
                     if p.get('symbol', '').upper() == clean
                     or clean in p.get('name', '').upper()]
        if not matches:
            return f"No DeFiLlama protocol found for {clean} — may not be a DeFi token."
        p    = matches[0]
        tvl  = (p.get('tvl') or 0) / 1e6
        ch1d = p.get('change_1d') or 0
        ch7d = p.get('change_7d') or 0
        return (
            f"Protocol: {p.get('name')} ({clean})  |  "
            f"TVL: ${tvl:.1f}M  |  "
            f"1d: {ch1d:+.1f}%  7d: {ch7d:+.1f}%  |  "
            f"Chain: {p.get('chain','Multi')}  Category: {p.get('category','')}"
        )
    except Exception as e:
        return f"DeFiLlama error: {e}"


def _tool_get_technical_data(symbol: str) -> str:
    """Fetch live TA from the exchange: price, RSI(14) 4H/1H, EMA20/50, volume ratio."""
    if _exchange is None:
        return "Exchange not connected."
    try:
        ohlcv = _exchange.fetch_ohlcv(symbol, '4h', limit=60)
        if not ohlcv or len(ohlcv) < 15:
            return f"Insufficient OHLCV data for {symbol}."
        closes  = [c[4] for c in ohlcv]
        volumes = [c[5] for c in ohlcv]
        price   = closes[-1]
        rsi4h   = _calc_rsi(closes, 14)
        ema20   = _calc_ema(closes, 20)
        ema50   = _calc_ema(closes, 50)
        avg_vol   = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes) / len(volumes)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        rsi1h_txt = "N/A"
        try:
            ohlcv_1h  = _exchange.fetch_ohlcv(symbol, '1h', limit=20)
            rsi1h_txt = f"{_calc_rsi([c[4] for c in ohlcv_1h], 14):.1f}"
        except Exception:
            pass
        last3   = closes[-3:]
        pattern = ("UPTREND"   if last3[-1] > last3[-2] > last3[-3] else
                   "DOWNTREND" if last3[-1] < last3[-2] < last3[-3] else "MIXED")
        return "\n".join([
            f"Symbol: {symbol}  |  Price: ${price:.6g}",
            f"RSI(14): 4H={rsi4h:.1f}  1H={rsi1h_txt}",
            f"EMA20: ${ema20:.6g}  EMA50: ${ema50:.6g}",
            f"Price vs EMA20: {'ABOVE (bullish)' if price > ema20 else 'BELOW (bearish)'}",
            f"EMA trend: {'BULLISH (EMA20>EMA50)' if ema20 > ema50 else 'BEARISH (EMA20<EMA50)'}",
            f"Volume ratio vs 20-bar avg: {vol_ratio:.1f}x",
            f"Recent 3-bar pattern: {pattern}",
        ])
    except Exception as e:
        return f"Technical data error for {symbol}: {e}"


def _tool_search_web(query: str) -> str:
    """DuckDuckGo instant answer search (no API key needed)."""
    try:
        r     = requests.get(
            "https://api.duckduckgo.com/",
            params={'q': query, 'format': 'json', 'no_html': '1', 'skip_disambig': '1'},
            timeout=5,
        )
        data  = r.json()
        parts = []
        if data.get("Abstract"):
            parts.append(data["Abstract"])
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(topic["Text"])
        result = " | ".join(parts)[:600] if parts else ""
        return result or f"No specific results for '{query}'."
    except Exception as e:
        return f"Search error: {e}"


# ── RSI / EMA helpers ─────────────────────────────────────────────────────────

def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))][-period:]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    return 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def _calc_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def _dispatch_tool(name: str, inputs: dict) -> str:
    if name == "get_trending_coins":
        return _tool_get_trending_coins()
    if name == "get_top_movers":
        return _tool_get_top_movers(inputs.get("direction", "gainers"))
    if name == "get_coin_data":
        return _tool_get_coin_data(inputs.get("coin_id", ""))
    if name == "get_technical_data":
        return _tool_get_technical_data(inputs.get("symbol", ""))
    if name == "get_defi_tvl":
        return _tool_get_defi_tvl(inputs.get("symbol", ""))
    if name == "search_web":
        return _tool_search_web(inputs.get("query", ""))
    return f"Unknown tool: {name}"


# ── Anthropic tool definitions ────────────────────────────────────────────────

_RESEARCH_TOOLS = [
    {
        "name": "get_trending_coins",
        "description": "Get the top 7 trending coins on CoinGecko right now. Call this FIRST — it reveals current market momentum.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_top_movers",
        "description": "Get top 12 price movers over the last 24 hours. Call for both gainers AND losers to find LONG and SHORT setups.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["gainers", "losers"],
                    "description": "'gainers' for LONG candidates (top pumps), 'losers' for SHORT candidates (top dumps)",
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "get_coin_data",
        "description": "Get detailed market data for a coin: price, market cap, volume, 24h/7d/30d % changes, ATH distance, and community sentiment. Use for fundamentals check after initial screening.",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin_id": {
                    "type": "string",
                    "description": "CoinGecko coin ID from the 'id:' field in previous results (e.g. 'bitcoin', 'fetch-ai', 'sui')",
                }
            },
            "required": ["coin_id"],
        },
    },
    {
        "name": "get_technical_data",
        "description": "Get real-time TA from the exchange: current price, RSI(14) on 4H and 1H, EMA20/EMA50, volume ratio. Essential for timing — call this before deciding to trade.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair with /USDT suffix: e.g. 'BTC/USDT', 'FET/USDT', 'SUI/USDT'",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_defi_tvl",
        "description": "Get DeFiLlama TVL data for DeFi protocol tokens (AAVE, UNI, CRV, etc.). Growing TVL = protocol health.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Token symbol without /USDT: e.g. 'AAVE', 'UNI', 'SUI', 'CRV'",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "search_web",
        "description": "Search for recent news, upcoming events, or token-specific developments. Use for your top 1-2 candidates to check for catalysts or hidden risks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "e.g. 'FET fetch.ai news July 2026' or 'SUI token unlock schedule 2026'",
                }
            },
            "required": ["query"],
        },
    },
]


# ── Dr. Sniper Research system prompt ─────────────────────────────────────────

RESEARCH_SYSTEM_PROMPT = """You are Dr. Sniper — an elite autonomous crypto trading analyst with 15+ years managing multi-million dollar portfolios.

MISSION: Independently scan the market, identify HIGH-CONVICTION trading setups, score them, and recommend entries.

REQUIRED RESEARCH PROCESS (follow in order):
1. DISCOVER: Call get_trending_coins() AND get_top_movers("gainers") AND get_top_movers("losers")
2. SCREEN: From results, select 3-5 most interesting candidates — look for momentum, volume, narrative alignment
3. DEEP DIVE: For each selected candidate call get_technical_data(symbol) for RSI/EMA timing
4. CONTEXT: Call get_coin_data(coin_id) for fundamental context. For DeFi tokens, also call get_defi_tvl(symbol)
5. NEWS: For your top 1-2 candidates, call search_web(query) to check for catalysts or hidden risks
6. SCORE: Build conviction score 0-100 for each

SCORING CRITERIA:
- Technical timing: RSI not overbought/oversold (4H RSI < 70 for LONG, > 35 for SHORT)
- EMA alignment: EMA20 > EMA50 supports LONG, EMA20 < EMA50 supports SHORT
- Volume: ratio > 1.5x is a positive signal
- Momentum: trending, recent catalyst, sector rotation narrative
- Risk: ATH distance, recent dump recovery, open interest conditions
- Macro: use your knowledge of BTC dominance and altcoin cycles

EXECUTION THRESHOLDS:
- score ≥ 78: High conviction → set execute: true (will be auto-traded)
- score 65-77: Interesting → set execute: false (human decides)
- score < 65: Not worth it → exclude from output entirely

CRITICAL RULES:
- Only include candidates where you have seen BOTH fundamental AND technical data
- An RSI > 75 on 4H is a hard veto for LONG entries
- An RSI < 30 on 4H is a hard veto for SHORT entries
- 3 weak signals = 0 trades

OUTPUT: Return ONLY valid JSON, no markdown, no explanation outside JSON:
{"candidates": [{"symbol": "XXX/USDT", "direction": "LONG", "score": 82, "reason": "max 15 words", "key_risk": "max 10 words", "execute": true}]}

Empty list is correct when nothing meets the bar. Quality > quantity."""


# ── Main research runner ──────────────────────────────────────────────────────

def run_claude_research(
    open_trades_count: int = 0,
    daily_pnl:         float = 0.0,
    fng:               int   = 50,
    max_trades:        int   = 3,
) -> list[dict]:
    """
    Run Dr. Sniper research session.
    Returns list of candidate dicts (empty if nothing found or error).
    Sends Telegram messages and optionally executes trades via callback.
    """
    if not _research_lock.acquire(blocking=False):
        print("[Research] ⚠️  Already running — skipping", flush=True)
        return []

    t0 = time.time()
    try:
        client = _get_client()
        if client is None:
            return []

        slots = max_trades - open_trades_count
        fng_label = ("Extreme Fear" if fng <= 25 else "Fear" if fng <= 45 else
                     "Neutral" if fng <= 55 else "Greed" if fng <= 75 else "Extreme Greed")

        print(
            f"[Research] 🔬 Starting | FNG={fng}({fng_label}) | "
            f"open={open_trades_count}/{max_trades} | pnl=${daily_pnl:+.2f}",
            flush=True,
        )

        prompt = (
            f"CURRENT MARKET CONTEXT:\n"
            f"- Fear & Greed: {fng}/100 ({fng_label})\n"
            f"- Open trades: {open_trades_count}/{max_trades} "
            f"({'NO SLOTS — set execute: false for ALL' if slots <= 0 else f'{slots} slot(s) available for execution'})\n"
            f"- Today P&L: ${daily_pnl:+.2f}\n\n"
            f"BEGIN RESEARCH NOW. Follow the required process: discover → screen → deep dive → score.\n"
            f"Return your JSON when complete."
        )

        messages        = [{"role": "user", "content": prompt}]
        tool_calls_made = 0
        raw             = ""

        # Evidence tracking — only execute if BOTH TA and fundamentals were fetched
        ta_evidence:          set[str] = set()   # symbols with successful get_technical_data call
        fundamental_evidence: set[str] = set()   # symbols with successful get_coin_data call

        while tool_calls_made < 12:
            response = client.messages.create(
                model="claude-haiku-4-5",
                system=RESEARCH_SYSTEM_PROMPT,
                max_tokens=1500,
                timeout=15.0,
                tools=_RESEARCH_TOOLS,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                # Claude finished — extract JSON
                text_block = next((b for b in response.content if hasattr(b, 'text')), None)
                raw = (text_block.text if text_block else "").strip()
                break

            # Handle tool calls (can be batched)
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls_made += 1
                result = _dispatch_tool(block.name, block.input)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })
                print(
                    f"[Research] 🔧 {block.name}"
                    f"({list(block.input.values())[:1] or ''}) "
                    f"→ {len(result)} chars",
                    flush=True,
                )
                # Record evidence ONLY from successful, usable tool results.
                # A result is usable only if it does NOT start with a known error prefix.
                _ERROR_PREFIXES = (
                    "Technical data error", "Exchange not connected",
                    "Insufficient OHLCV", "CoinGecko coin data error",
                    "CoinGecko trending error", "CoinGecko movers error",
                    "DeFiLlama error", "No DeFiLlama", "Search error",
                    "No specific results",
                )
                result_ok = not any(result.startswith(p) for p in _ERROR_PREFIXES)

                if block.name == "get_technical_data" and result_ok:
                    # Result first line: "Symbol: XYZ/USDT  |  Price: …"
                    sym = str(block.input.get("symbol", "")).strip().upper()
                    sym = sym if "/" in sym else f"{sym}/USDT"
                    if _VALID_SYMBOL_RE.match(sym):
                        ta_evidence.add(sym)
                        print(f"[Research] 📊 TA evidence recorded: {sym}", flush=True)
                    else:
                        print(f"[Research] ⚠️  TA result for unrecognised symbol format '{sym}' — not recorded", flush=True)

                elif block.name == "get_coin_data" and result_ok:
                    # Result first line: "Name: Fetch.ai (FET/USDT)"  → extract FET/USDT
                    canon_match = _re.search(r'\(([A-Z0-9]{2,12}/USDT)\)', result)
                    if canon_match:
                        canon_sym = canon_match.group(1)
                        fundamental_evidence.add(canon_sym)
                        print(f"[Research] 📋 Fundamental evidence recorded: {canon_sym}", flush=True)
                    else:
                        print(f"[Research] ⚠️  get_coin_data result missing canonical symbol — not recorded", flush=True)
                elif block.name in ("get_technical_data", "get_coin_data") and not result_ok:
                    print(f"[Research] ⚠️  {block.name} returned error — evidence NOT recorded", flush=True)

            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": tool_results + [
                    {
                        "type": "text",
                        "text": (
                            "You have all the data you need. Now output your verdict. "
                            "Reply with ONLY a raw JSON object and nothing else — "
                            "no markdown, no analysis, no code fences. Exactly this shape:\n"
                            '{"candidates": [{"symbol": "XXX/USDT", "direction": "LONG", '
                            '"score": <int 0-100>, "reason": "<15 words max>", '
                            '"key_risk": "<10 words max>", "execute": <true|false>}]}'
                        ),
                    }
                ]},
            ]

        latency = int(time.time() - t0)
        print(
            f"[Research] ✅ Done in {latency}s | {tool_calls_made} tool calls | "
            f"TA evidence: {ta_evidence} | Fund evidence: {fundamental_evidence}",
            flush=True,
        )

        # ── Parse JSON ────────────────────────────────────────────────────────
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            parsed     = json.loads(raw)
            candidates = parsed.get("candidates", [])
            if not isinstance(candidates, list):
                raise ValueError("candidates is not a list")
        except Exception as e:
            print(f"[Research] ⚠️  JSON parse error: {e}\nRaw: {raw[:300]}", flush=True)
            candidates = []

        research_stats['runs']             += 1
        research_stats['last_run']          = time.strftime('%H:%M')
        research_stats['candidates_found'] += len(candidates)

        if not candidates:
            print("[Research] 📭 No qualifying candidates this run", flush=True)
            return []

        _process_candidates(candidates, slots, ta_evidence, fundamental_evidence)
        return candidates

    except Exception as e:
        print(f"[Research] ❌ Unexpected error: {e}", flush=True)
        return []
    finally:
        _research_lock.release()


# ── Candidate processing ──────────────────────────────────────────────────────

import re as _re

_VALID_SYMBOL_RE = _re.compile(r'^[A-Z0-9]{2,12}/USDT$')


def _validate_candidate(c: dict) -> tuple[bool, str]:
    """
    Strict deterministic schema check — returns (ok, rejection_reason).
    All validation is code-enforced; nothing relies solely on the LLM prompt.
    """
    symbol    = str(c.get("symbol", "")).strip().upper()
    direction = str(c.get("direction", "")).strip()   # NOT uppercased — must arrive as LONG/SHORT
    score_raw = c.get("score")
    reason    = str(c.get("reason", "")).strip()
    key_risk  = str(c.get("key_risk", "")).strip()

    # 1 — symbol format: BASE/USDT, base 2-12 alphanumeric chars
    if not _VALID_SYMBOL_RE.match(symbol):
        return False, f"bad symbol format '{symbol}'"

    # 2 — direction must be exactly LONG or SHORT (no silent normalisation)
    if direction not in ("LONG", "SHORT"):
        return False, f"invalid direction '{direction}' (must be exactly 'LONG' or 'SHORT')"

    # 3 — score must be a true JSON integer (not bool, float, string)
    #     bool is a subclass of int in Python — explicitly excluded.
    #     float 78.9 and string "82" are NOT accepted.
    score_raw = c.get("score")
    if not isinstance(score_raw, int) or isinstance(score_raw, bool):
        return False, (
            f"score must be a JSON integer, got {type(score_raw).__name__} '{score_raw}'"
        )
    score = score_raw
    if not (0 <= score <= 100):
        return False, f"score {score} out of range 0-100"

    # 4 — reason must be nonempty, ≤ 200 chars
    if not reason:
        return False, "empty reason"
    if len(reason) > 200:
        return False, f"reason too long ({len(reason)} chars)"

    # key_risk is optional but bounded
    if len(key_risk) > 200:
        return False, f"key_risk too long ({len(key_risk)} chars)"

    # 5 — execute must be exactly a JSON boolean (not a string, not 1/0)
    #     Missing field defaults to False (not executable).
    execute_raw = c.get("execute", False)
    if not isinstance(execute_raw, bool):
        return False, (
            f"execute field must be a JSON boolean, got {type(execute_raw).__name__} "
            f"'{execute_raw}'"
        )

    return True, ""


def _process_candidates(
    candidates:          list,
    slots_available:     int,
    ta_evidence:         "set[str]",
    fundamental_evidence: "set[str]",
) -> None:
    """Execute auto-trades and/or send Telegram alerts."""
    medals    = ["🥇", "🥈", "🥉", "4\\.", "5\\."]
    executed  = 0
    msg_lines = ["🔬 *Dr\\. Sniper Research*", "━━━━━━━━━━━━━━━━━━"]

    for i, c in enumerate(candidates[:5]):
        # ── Strict schema validation ──────────────────────────────────────────
        ok, rejection = _validate_candidate(c)
        if not ok:
            print(f"[Research] ⚠️  Candidate {i} rejected — {rejection}", flush=True)
            continue

        symbol    = str(c["symbol"]).strip().upper()
        direction = str(c["direction"]).strip().upper()
        score     = int(c["score"])
        reason    = str(c.get("reason", "")).strip()[:200]
        key_risk  = str(c.get("key_risk", "")).strip()[:200]
        # execute is already validated as a strict bool by _validate_candidate
        do_exec   = c.get("execute", False) is True

        if score < 65:
            continue

        # ── Save to stats ─────────────────────────────────────────────────────
        research_stats['last_candidates'].append({
            'ts': time.strftime('%H:%M'), 'symbol': symbol,
            'direction': direction, 'score': score, 'reason': reason,
        })

        medal    = medals[i] if i < len(medals) else f"{i+1}\\."
        dir_icon = "📈" if direction == "LONG" else "📉"
        name     = symbol.replace("/USDT", "")
        risk_ln  = f"\n⚠️ _{key_risk}_" if key_risk else ""

        # ── Auto-execute — requires server-side TA + fundamental evidence ─────
        base = symbol.replace("/USDT", "")
        has_ta          = symbol in ta_evidence or base in ta_evidence
        has_fundamental = (
            symbol in fundamental_evidence
            or base in fundamental_evidence
            or base.lower() in fundamental_evidence
        )

        if do_exec and score >= 78 and executed < slots_available and _auto_execute and _execute_trade_fn:
            if not has_ta:
                print(
                    f"[Research] ⛔ {symbol} — no TA evidence (get_technical_data not called) "
                    "— downgrading to alert-only",
                    flush=True,
                )
                action = "⚠️ *חסר נתוני TA — התראה בלבד*"
            elif not has_fundamental:
                print(
                    f"[Research] ⛔ {symbol} — no fundamental evidence (get_coin_data not called) "
                    "— downgrading to alert-only",
                    flush=True,
                )
                action = "⚠️ *חסר נתוני פונדמנטלס — התראה בלבד*"
            else:
                try:
                    exec_ok = _execute_trade_fn(
                        symbol=symbol, direction=direction,
                        claude_score=score, reason=reason, key_risk=key_risk,
                    )
                    if exec_ok:
                        research_stats['trades_executed'] += 1
                        executed += 1
                        action = "🤖 *ביצוע אוטומטי*"
                    else:
                        print(f"[Research] ⚠️  execute returned False for {symbol} — not counted", flush=True)
                        action = "⚠️ *ביצוע נדחה*"
                except Exception as e:
                    print(f"[Research] ⚠️  execute exception {symbol}: {e}", flush=True)
                    action = "⚠️ *ביצוע נכשל*"
        else:
            action = "📊 *ממתין לאישורך*"

        msg_lines += [
            f"\n{medal} *{name}* {dir_icon} {direction} — score `{score}`",
            f"✅ _{reason}_{risk_ln}",
            f"→ {action}",
        ]

    if len(msg_lines) > 2 and _send_msg_fn:
        research_stats['alerts_sent'] += 1
        _send_msg_fn("\n".join(l for l in msg_lines if l))


# ── Stats summary for /research ───────────────────────────────────────────────

def research_summary() -> str:
    r    = research_stats
    mode = "🟢 AUTO\\-EXECUTE" if _auto_execute else "🟡 ALERT\\-ONLY"
    lines = [
        "🔬 *Dr\\. Sniper Research*",
        f"Mode: {mode}",
        f"Runs: {r['runs']} | Candidates: {r['candidates_found']} | "
        f"Executed: {r['trades_executed']} | Alerts: {r['alerts_sent']}",
        f"Last run: {r['last_run'] or 'never'}",
        "",
        "Commands: `/research` — run now  |  `/research off` — alerts only  |  `/research on` — auto\\-execute",
    ]
    candidates = list(r['last_candidates'])
    if candidates:
        lines.append("\n*Last candidates:*")
        for c in reversed(candidates):
            icon = "📈" if c['direction'] == 'LONG' else "📉"
            lines.append(
                f"  {icon} `{c['ts']}` *{c['symbol']}* "
                f"score={c['score']} — _{c['reason']}_"
            )
    return "\n".join(lines)
