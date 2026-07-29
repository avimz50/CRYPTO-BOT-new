"""
claude_gate.py — Claude AI Gate for trade entry decisions.

Evaluates every trade signal before entry:
  - Receives pre-computed bot signals (score, RSI, FNG, regime, reason)
  - Returns (approved, claude_score 0-100, reason_text)
  - Combined score = 0.6 × bot_score + 0.4 × claude_score  (for strategies with bot_score)
  - Timeout: 6 seconds → fallback approve so API outage never blocks trading
  - Model: claude-haiku-4-5 (fastest + cheapest, ~0.5s response time)
"""

import os
import json
import time
import anthropic

# ── singleton client (lazy-init) ─────────────────────────────────────────────
_client: anthropic.Anthropic | None = None
_client_init_failed = False   # don't retry after a bad key

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
        print("[ClaudeGate] ✅ Anthropic client initialised", flush=True)
    except Exception as e:
        print(f"[ClaudeGate] ❌ client init error: {e}", flush=True)
        _client_init_failed = True
    return _client


# ── stats (for Telegram summary / dashboard) ─────────────────────────────────
gate_stats: dict = {
    'approved': 0,
    'rejected': 0,
    'fallback': 0,
    'total_latency_ms': 0,
    'calls': 0,
}


# ── FNG label ─────────────────────────────────────────────────────────────────
def _fng_label(fng: int) -> str:
    if fng <= 25:  return "Extreme Fear"
    if fng <= 45:  return "Fear"
    if fng <= 55:  return "Neutral"
    if fng <= 75:  return "Greed"
    return "Extreme Greed"


# ── main gate function ────────────────────────────────────────────────────────
def claude_trade_gate(
    symbol:      str,
    direction:   str,         # 'LONG' or 'SHORT'
    strategy:    str,         # 'Swing' | 'Scalp' | 'Breakout' | 'Velocity'
    price:       float,
    bot_score:   int   = 0,   # 0 = not available (Scalp/Cliff)
    regime:      str   = 'NEUTRAL',
    fng:         int   = 50,
    btc_above_ema: bool = True,
    rsi:         float | None = None,
    reason:      str   = '',
    extra:       dict  | None = None,   # strategy-specific signals
    daily_pnl:   float = 0.0,
    open_trades: int   = 0,
) -> tuple[bool, int, str]:
    """
    Returns (approved: bool, claude_score: int 0-100, reason: str).
    On any error / timeout → (True, 70, "fallback approve").
    """
    client = _get_client()
    if client is None:
        gate_stats['fallback'] += 1
        return True, 70, "gate disabled — API key missing"

    # Build extra lines
    extra_txt = ""
    if extra:
        for k, v in extra.items():
            extra_txt += f"\n- {k}: {v}"

    btc_pos = "above" if btc_above_ema else "below"
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
    score_line = f"\n- Bot technical score: {bot_score}/100" if bot_score > 0 else ""

    prompt = (
        f"You are a crypto futures trade validator. Evaluate coherence of this signal.\n\n"
        f"SIGNAL:\n"
        f"- Symbol: {symbol} | Direction: {direction} | Strategy: {strategy}\n"
        f"- Entry: ${price}{score_line}\n"
        f"- Regime: {regime} | BTC: {btc_pos} EMA20(4H)\n"
        f"- FNG: {fng}/100 ({_fng_label(fng)}) | RSI: {rsi_txt}\n"
        f"- Trigger: {reason}\n"
        f"- Today P&L: ${daily_pnl:+.2f} | Open trades: {open_trades}"
        f"{extra_txt}\n\n"
        f"Score 0-100 based on: direction vs regime alignment, RSI sensibility, "
        f"signal coherence, risk conditions. Approve if score >= 65.\n\n"
        f"Reply ONLY with JSON (no markdown):\n"
        f'{{\"score\": <int>, \"approve\": <bool>, \"reason\": \"<max 10 words>\"}}'
    )

    t0 = time.time()
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=80,
            timeout=6.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data         = json.loads(raw)
        claude_score = max(0, min(100, int(data.get("score", 70))))
        approved     = bool(data.get("approve", claude_score >= 65))
        reason_out   = str(data.get("reason", ""))[:80]

        latency_ms = int((time.time() - t0) * 1000)
        gate_stats['calls']             += 1
        gate_stats['total_latency_ms']  += latency_ms
        gate_stats['approved' if approved else 'rejected'] += 1

        icon = "✅" if approved else "⛔"
        print(
            f"[ClaudeGate] {icon} {symbol} {direction}/{strategy} | "
            f"claude={claude_score} | {latency_ms}ms | {reason_out}",
            flush=True
        )
        return approved, claude_score, reason_out

    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        gate_stats['fallback'] += 1
        print(f"[ClaudeGate] ⚠️  {symbol} error ({latency_ms}ms): {e} — fallback approve", flush=True)
        return True, 70, "API error — fallback approve"


# ── combined score helper ─────────────────────────────────────────────────────
def combined_score(bot_score: int, claude_score: int,
                   w_bot: float = 0.6, w_claude: float = 0.4) -> int:
    """Weighted average. Returns int 0-100."""
    return round(w_bot * bot_score + w_claude * claude_score)


# ── stats summary (for Telegram /stats command) ───────────────────────────────
def gate_summary() -> str:
    c = gate_stats['calls']
    if c == 0:
        return "ClaudeGate: no calls yet"
    avg_ms = gate_stats['total_latency_ms'] // c
    return (
        f"🤖 *Claude Gate*\n"
        f"✅ Approved: {gate_stats['approved']} | "
        f"⛔ Rejected: {gate_stats['rejected']} | "
        f"⚠️ Fallback: {gate_stats['fallback']}\n"
        f"⏱ Avg latency: {avg_ms}ms"
    )
