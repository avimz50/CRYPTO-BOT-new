"""
claude_gate.py — Claude AI Gate: Dr. Sniper trading intelligence.

Evaluates every trade signal before entry using:
  - "Dr. Sniper" expert persona with deep crypto expertise
  - Live market enrichment: funding rate, order book imbalance, 24h stats
  - Web search tool for on-demand news & sentiment (DuckDuckGo)
  - Extended response: score, approve, reason, key_risk

Returns (approved, claude_score 0-100, reason, key_risk)
Timeout: 10s per API call → fallback approve on error/timeout
Model: claude-haiku-4-5 (fastest + cheapest)
"""

import os
import json
import time
import requests
from collections import deque
import anthropic
import state_store

GATE_STATS_FILE = "data/gate_stats.json"

# ── singleton client (lazy-init) ─────────────────────────────────────────────
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
        print("[ClaudeGate] ⚠️  ANTHROPIC_API_KEY missing — gate disabled", flush=True)
        _client_init_failed = True
        return None
    try:
        _client = anthropic.Anthropic(api_key=api_key)
        print("[ClaudeGate] ✅ Anthropic client initialised (Dr. Sniper ready)", flush=True)
    except Exception as e:
        print(f"[ClaudeGate] ❌ client init error: {e}", flush=True)
        _client_init_failed = True
    return _client


# ── Exchange reference (injected from bot.py after init) ─────────────────────
_exchange = None

def set_exchange(ex) -> None:
    """Register the ccxt exchange instance for live market data enrichment."""
    global _exchange
    _exchange = ex
    print("[ClaudeGate] ✅ Exchange registered for signal enrichment", flush=True)


# ── Dr. Sniper system prompt ──────────────────────────────────────────────────
CLAUDE_SYSTEM_PROMPT = """You are Dr. Sniper — an elite crypto futures trader with 15+ years of experience managing multi-million dollar portfolios across BTC, ETH, and altcoin markets.

Your expertise:
• Macro market regimes (bull/bear cycles, liquidity conditions, BTC dominance)
• Momentum and breakout patterns (real breakouts vs. bull/bear traps)
• Funding rate dynamics (extreme rates signal imminent reversals)
• Order book structure and liquidity walls
• Market sentiment (Fear & Greed extremes as contrarian indicators)
• News catalysts and on-chain events that move prices

Your job: evaluate trade signals with professional precision. You consider ALL available data — technical, macro, sentiment, funding, and news. You are strict: mediocre setups get rejected. Only high-conviction, asymmetric entries deserve capital. Be concise but precise. Your risk assessment protects the account."""


# ── stats ─────────────────────────────────────────────────────────────────────
def _load_gate_stats() -> dict:
    """Load persisted gate stats from object storage; fall back to zeroes."""
    default = {
        'approved': 0, 'rejected': 0, 'fallback': 0,
        'total_latency_ms': 0, 'calls': 0, 'web_searches': 0,
        'last_decisions': [],
    }
    data = state_store.load_state('gate_stats', GATE_STATS_FILE, default)
    # last_decisions stored as list → restore as deque
    decisions = deque(data.get('last_decisions', []), maxlen=3)
    return {
        'approved':         int(data.get('approved', 0)),
        'rejected':         int(data.get('rejected', 0)),
        'fallback':         int(data.get('fallback', 0)),
        'total_latency_ms': int(data.get('total_latency_ms', 0)),
        'calls':            int(data.get('calls', 0)),
        'web_searches':     int(data.get('web_searches', 0)),
        'last_decisions':   decisions,
    }


def _save_gate_stats() -> None:
    """Persist gate stats to object storage (best-effort)."""
    try:
        snapshot = {k: (list(v) if isinstance(v, deque) else v)
                    for k, v in gate_stats.items()}
        state_store.save_state('gate_stats', snapshot, GATE_STATS_FILE)
    except Exception as _e:
        print(f"[ClaudeGate] ⚠️  gate_stats save error: {_e}", flush=True)


gate_stats: dict = _load_gate_stats()


# ── FNG label ─────────────────────────────────────────────────────────────────
def _fng_label(fng: int) -> str:
    if fng <= 25:  return "Extreme Fear"
    if fng <= 45:  return "Fear"
    if fng <= 55:  return "Neutral"
    if fng <= 75:  return "Greed"
    return "Extreme Greed"


# ── Web search via DuckDuckGo Instant Answer (no API key required) ────────────
def _search_web(query: str) -> str:
    """DuckDuckGo Instant Answer API — free, no key needed."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={'q': query, 'format': 'json', 'no_html': '1', 'skip_disambig': '1'},
            timeout=4,
        )
        data = r.json()
        parts: list[str] = []
        if data.get("Abstract"):
            parts.append(data["Abstract"])
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(topic["Text"])
        result = " | ".join(parts)[:600] if parts else ""
        return result or f"No specific results for '{query}'. Use technical data only."
    except Exception as e:
        return f"Search unavailable ({e}). Use technical data only."


# ── Live market enrichment from exchange ─────────────────────────────────────
def _enrich_signal(symbol: str) -> dict:
    """Fetch funding rate, order book imbalance, 24h stats from ccxt exchange."""
    if _exchange is None:
        return {}
    data: dict = {}
    try:
        # Funding rate — tells us if the market is over-leveraged
        try:
            fr   = _exchange.fetch_funding_rate(symbol)
            rate = fr.get('fundingRate') or fr.get('funding_rate')
            if rate is not None:
                pct  = float(rate) * 100
                mood = ("BULLISH EXCESS — longs may be squeezed" if pct > 0.05 else
                        "BEARISH EXCESS — shorts may be squeezed" if pct < -0.02 else
                        "neutral")
                data['funding_rate'] = f"{pct:+.4f}% ({mood})"
        except Exception:
            pass

        # Order book top-5 bid/ask imbalance
        try:
            ob      = _exchange.fetch_order_book(symbol, limit=10)
            bid_vol = sum(b[1] for b in ob.get('bids', [])[:5])
            ask_vol = sum(a[1] for a in ob.get('asks', [])[:5])
            total   = bid_vol + ask_vol
            if total > 0:
                imb   = (bid_vol - ask_vol) / total
                label = ("strong buy pressure"  if imb >  0.20 else
                         "buy pressure"          if imb >  0.05 else
                         "strong sell pressure"  if imb < -0.20 else
                         "sell pressure"         if imb < -0.05 else
                         "balanced")
                data['ob_imbalance'] = f"{imb:+.2f} ({label})"
        except Exception:
            pass

        # 24h ticker: price change + volume
        try:
            ticker = _exchange.fetch_ticker(symbol)
            if ticker.get('percentage') is not None:
                data['24h_change'] = f"{ticker['percentage']:+.1f}%"
            if ticker.get('quoteVolume') is not None:
                data['24h_volume'] = f"${ticker['quoteVolume'] / 1_000_000:.1f}M"
        except Exception:
            pass

    except Exception as e:
        print(f"[ClaudeGate] ⚠️  enrich error {symbol}: {e}", flush=True)
    return data


# ── Web search tool definition ────────────────────────────────────────────────
_TOOLS = [
    {
        "name": "search_web",
        "description": (
            "Search the web for recent news, events, or sentiment about a crypto token. "
            "Use when you need context about recent developments, upcoming token unlocks, "
            "protocol updates, regulatory news, or anything that could affect the trade."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "query": {
                    "type":        "string",
                    "description": "Search query, e.g. 'BTC bitcoin news today' or 'FET fetch.ai token unlock 2026'"
                }
            },
            "required": ["query"],
        },
    }
]


# ── main gate function ────────────────────────────────────────────────────────
def claude_trade_gate(
    symbol:        str,
    direction:     str,           # 'LONG' or 'SHORT'
    strategy:      str,           # 'Swing' | 'Scalp' | 'Breakout' | 'Velocity'
    price:         float,
    bot_score:     int   = 0,     # 0 = not applicable (Scalp/Velocity)
    regime:        str   = 'NEUTRAL',
    fng:           int   = 50,
    btc_above_ema: bool  = True,
    rsi:           float | None = None,
    reason:        str   = '',
    extra:         dict  | None = None,
    daily_pnl:     float = 0.0,
    open_trades:   int   = 0,
) -> tuple[bool, int, str, str]:
    """
    Returns (approved: bool, claude_score: int 0-100, reason: str, key_risk: str).
    On any error / timeout → (True, 70, "fallback approve", "").
    """
    client = _get_client()
    if client is None:
        gate_stats['fallback'] += 1
        _save_gate_stats()
        return False, 0, "gate unavailable (no API key) — fail closed", ""

    # ── Enrich with live market data ──────────────────────────────────────────
    enrichment = _enrich_signal(symbol)

    # ── Build prompt ──────────────────────────────────────────────────────────
    btc_pos  = "ABOVE" if btc_above_ema else "BELOW"
    rsi_txt  = f"{rsi:.1f}" if rsi is not None else "N/A"
    score_ln = f"\n- Bot technical score: {bot_score}/100" if bot_score > 0 else ""

    extra_lines = ""
    if extra:
        for k, v in extra.items():
            extra_lines += f"\n- {k}: {v}"

    market_section = ""
    if enrichment:
        market_section = "\n\nLIVE MARKET DATA:"
        for k, v in enrichment.items():
            market_section += f"\n- {k.replace('_', ' ').title()}: {v}"

    prompt = (
        f"TRADE SIGNAL — evaluate for entry.\n\n"
        f"SIGNAL:\n"
        f"- Symbol: {symbol} | Direction: {direction} | Strategy: {strategy}\n"
        f"- Entry price: ${price}{score_ln}\n"
        f"- Market regime: {regime} | BTC vs EMA20(4H): {btc_pos}\n"
        f"- Fear & Greed: {fng}/100 ({_fng_label(fng)}) | RSI: {rsi_txt}\n"
        f"- Trigger: {reason}"
        f"{extra_lines}"
        f"{market_section}\n\n"
        f"PORTFOLIO CONTEXT:\n"
        f"- Today P&L: ${daily_pnl:+.2f} | Open trades: {open_trades}/3\n\n"
        f"TASK: Score 0-100 on signal quality (regime alignment, RSI context, "
        f"momentum quality, funding conditions, risk/reward). Approve if score >= 65.\n"
        f"If you need recent news or events to make a confident decision, "
        f"call search_web first.\n\n"
        f"Reply ONLY with JSON (no markdown):\n"
        f'{{"score": <int>, "approve": <bool>, '
        f'"reason": "<15 words max>", "key_risk": "<10 words max>"}}'
    )

    t0          = time.time()
    web_searched = False
    try:
        messages = [{"role": "user", "content": prompt}]

        # ── First API call ────────────────────────────────────────────────────
        response = client.messages.create(
            model="claude-haiku-4-5",
            system=CLAUDE_SYSTEM_PROMPT,
            max_tokens=300,
            timeout=10.0,
            tools=_TOOLS,
            messages=messages,
        )

        # ── Handle one tool call (web search) ─────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_block = next(
                (b for b in response.content if b.type == "tool_use"), None
            )
            if tool_block and tool_block.name == "search_web":
                query         = tool_block.input.get("query", symbol)
                search_result = _search_web(query)
                web_searched  = True
                gate_stats['web_searches'] += 1
                print(
                    f"[ClaudeGate] 🔍 web search: '{query}' "
                    f"→ {len(search_result)} chars",
                    flush=True,
                )
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type":        "tool_result",
                                "tool_use_id": tool_block.id,
                                "content":     search_result,
                            }
                        ],
                    },
                ]
                response = client.messages.create(
                    model="claude-haiku-4-5",
                    system=CLAUDE_SYSTEM_PROMPT,
                    max_tokens=300,
                    timeout=10.0,
                    tools=_TOOLS,
                    messages=messages,
                )

        # ── Parse JSON ────────────────────────────────────────────────────────
        text_block = next((b for b in response.content if hasattr(b, 'text')), None)
        raw = (text_block.text if text_block else "").strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        parsed       = json.loads(raw)
        claude_score = max(0, min(100, int(parsed.get("score", 70))))
        approved     = bool(parsed.get("approve", claude_score >= 65))
        reason_out   = str(parsed.get("reason",   ""))[:80]
        key_risk     = str(parsed.get("key_risk", ""))[:60]

        latency_ms = int((time.time() - t0) * 1000)
        gate_stats['calls']            += 1
        gate_stats['total_latency_ms'] += latency_ms
        gate_stats['approved' if approved else 'rejected'] += 1

        # Store last decision for /gate display
        gate_stats['last_decisions'].append({
            'ts':         time.strftime('%H:%M'),
            'symbol':     symbol,
            'direction':  direction,
            'strategy':   strategy,
            'score':      claude_score,
            'approved':   approved,
            'reason':     reason_out,
            'key_risk':   key_risk,
            'web':        web_searched,
            'latency_ms': latency_ms,
        })

        icon     = "✅" if approved else "⛔"
        web_note = " 🔍" if web_searched else ""
        print(
            f"[ClaudeGate] {icon} {symbol} {direction}/{strategy} | "
            f"claude={claude_score} | {latency_ms}ms{web_note} | {reason_out}",
            flush=True,
        )
        _save_gate_stats()
        return approved, claude_score, reason_out, key_risk

    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        gate_stats['fallback'] += 1
        _save_gate_stats()
        print(
            f"[ClaudeGate] ⛔ {symbol} error ({latency_ms}ms): {e} — fail closed",
            flush=True,
        )
        return False, 0, "gate unavailable (API error) — fail closed", ""


# ── combined score helper ─────────────────────────────────────────────────────
def combined_score(bot_score: int, claude_score: int,
                   w_bot: float = 0.6, w_claude: float = 0.4) -> int:
    """Weighted average. Returns int 0-100."""
    return round(w_bot * bot_score + w_claude * claude_score)


# ── stats summary for /gate Telegram command ──────────────────────────────────
def gate_summary() -> str:
    c = gate_stats['calls']
    if c == 0:
        return "🤖 *Claude Gate* — no calls yet"
    avg_ms = gate_stats['total_latency_ms'] // max(c, 1)
    web    = gate_stats['web_searches']
    lines  = [
        "🤖 *Dr\\. Sniper Gate*",
        f"✅ Approved: {gate_stats['approved']} | "
        f"⛔ Rejected: {gate_stats['rejected']} | "
        f"⚠️ Fallback: {gate_stats['fallback']}",
        f"🔍 Web searches: {web} | ⏱ Avg latency: {avg_ms}ms",
    ]
    decisions = list(gate_stats['last_decisions'])
    if decisions:
        lines.append("\n*Latest decisions:*")
        for d in reversed(decisions):   # newest first
            icon     = "✅" if d['approved'] else "⛔"
            web_tag  = " 🔍" if d.get('web') else ""
            risk_ln  = f"\n   ⚠️ _{d['key_risk']}_" if d.get('key_risk') else ""
            lines.append(
                f"{icon} `{d['ts']}` *{d['symbol']}* {d['direction']} "
                f"\\({d['strategy']}\\) score={d['score']}{web_tag}\n"
                f"   _{d['reason']}_"
                f"{risk_ln}"
            )
    return "\n".join(lines)
