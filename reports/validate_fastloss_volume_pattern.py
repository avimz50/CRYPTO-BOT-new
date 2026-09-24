#!/usr/bin/env python3
"""Reproduce the post-fix FastLoss volume-climax validation tables."""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "artifacts/bot-dashboard/public/trade_audit.json"
CUTOFF = "2026-09-14"


def extract_volume(trade: dict) -> tuple[float | None, str | None]:
    value = trade.get("volume_ratio")
    if isinstance(value, (int, float)):
        return float(value), "volume_ratio"

    breakdown = trade.get("score_breakdown", "")
    patterns = (
        r"EMA_bypass\(vol×(\d+(?:\.\d+)?)",
        r"Vol=\d+/\d+\(×(\d+(?:\.\d+)?)",
        r"(?:15m\s+)?Vol×(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, breakdown, re.IGNORECASE)
        if match:
            return float(match.group(1)), "score_breakdown"
    return None, None


def extract_fvg_score(trade: dict) -> int | None:
    match = re.search(
        r"(?:^|\|\s*)FVG=([+]?-?\d+)",
        trade.get("score_breakdown", ""),
    )
    return int(match.group(1).replace("+", "")) if match else None


def extract_ema_bypass(trade: dict) -> bool | None:
    breakdown = trade.get("score_breakdown", "")
    # The standard score format always includes FVG and emits EMA_bypass only
    # when it fired. Breakout/Major Watch formats expose neither status.
    if not re.search(r"(?:^|\|\s*)FVG=", breakdown):
        return None
    return "EMA_bypass" in breakdown


def outcome_metrics(trades: list[dict]) -> dict:
    count = len(trades)
    wins = sum(trade["_net_pnl"] > 0 for trade in trades)
    fast_losses = sum(trade.get("close_reason") == "FastLoss" for trade in trades)
    return {
        "count": count,
        "wins": wins,
        "win_rate": 100 * wins / count if count else None,
        "average_net_pnl": (
            sum(trade["_net_pnl"] for trade in trades) / count if count else None
        ),
        "fast_losses": fast_losses,
        "fast_loss_rate": 100 * fast_losses / count if count else None,
    }


def exit_metrics(trades: list[dict]) -> dict:
    rows = []
    for trade in trades:
        entry = trade.get("entry_price")
        close = trade.get("close_price")
        atr = trade.get("atr")
        if not (
            isinstance(entry, (int, float))
            and entry > 0
            and isinstance(close, (int, float))
            and isinstance(atr, (int, float))
            and atr > 0
            and trade.get("opened_at")
            and trade.get("closed_at")
        ):
            continue
        rows.append(
            {
                "duration_min": float(trade["duration_min"]),
                "adverse_pct": (entry - close) / entry * 100,
                "adverse_atr": (entry - close) / atr,
            }
        )

    result = {"count": len(rows)}
    for field in ("duration_min", "adverse_pct", "adverse_atr"):
        values = [row[field] for row in rows]
        result[field] = {
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }
    return result


def main() -> None:
    payload = json.loads(AUDIT_PATH.read_text())
    all_trades = payload["trades"]
    clean_longs = [
        trade.copy()
        for trade in all_trades
        if trade.get("direction") == "LONG"
        and (trade.get("opened_at") or "") >= CUTOFF
    ]

    missing = Counter()
    for trade in clean_longs:
        trade["_volume"], trade["_volume_source"] = extract_volume(trade)
        trade["_ema_bypass"] = extract_ema_bypass(trade)
        trade["_fvg_score"] = extract_fvg_score(trade)
        trade["_net_pnl"] = float(
            trade.get("net_pnl_usd", trade.get("pnl_usd", 0)) or 0
        )
        if trade["_volume"] is None:
            missing["volume"] += 1
        if trade["_ema_bypass"] is None:
            missing["ema_bypass"] += 1
        if trade["_fvg_score"] is None:
            missing["fvg_score"] += 1
        if not (
            isinstance(trade.get("atr"), (int, float)) and trade["atr"] > 0
        ):
            missing["atr"] += 1
        if not (trade.get("opened_at") and trade.get("closed_at")):
            missing["timestamps"] += 1

    qualifying = [
        trade
        for trade in clean_longs
        if trade["_volume"] is not None
        and trade["_ema_bypass"] is not None
        and trade["_fvg_score"] is not None
        and isinstance(trade.get("atr"), (int, float))
        and trade["atr"] > 0
        and trade.get("opened_at")
        and trade.get("closed_at")
    ]

    buckets = (
        ("<2x", lambda value: value < 2),
        ("2–3x", lambda value: 2 <= value < 3),
        ("3–5x", lambda value: 3 <= value < 5),
        ("5–10x", lambda value: 5 <= value <= 10),
        (">10x", lambda value: value > 10),
    )
    signatures = (
        (
            "EMA bypass + positive FVG",
            lambda trade: trade["_ema_bypass"] and trade["_fvg_score"] > 0,
        ),
        (
            "EMA bypass only",
            lambda trade: trade["_ema_bypass"] and trade["_fvg_score"] <= 0,
        ),
        (
            "Positive FVG only",
            lambda trade: not trade["_ema_bypass"] and trade["_fvg_score"] > 0,
        ),
        (
            "Neither",
            lambda trade: not trade["_ema_bypass"] and trade["_fvg_score"] <= 0,
        ),
    )

    result = {
        "source_updated": payload.get("updated"),
        "audit_trade_count": len(all_trades),
        "clean_long_count": len(clean_longs),
        "qualifying_count": len(qualifying),
        "missing": dict(missing),
        "volume_buckets": {
            name: outcome_metrics(
                [trade for trade in qualifying if predicate(trade["_volume"])]
            )
            for name, predicate in buckets
        },
        "entry_signatures": {
            name: outcome_metrics(
                [trade for trade in qualifying if predicate(trade)]
            )
            for name, predicate in signatures
        },
        "loss_exit_comparison": {
            reason: exit_metrics(
                [
                    trade
                    for trade in clean_longs
                    if trade.get("close_reason") == reason
                ]
            )
            for reason in ("FastLoss", "SL", "MaxDuration")
        },
        "qualifying_trades": [
            {
                "opened_at": trade["opened_at"],
                "symbol": trade["symbol"],
                "volume_ratio": trade["_volume"],
                "volume_source": trade["_volume_source"],
                "ema_bypass": trade["_ema_bypass"],
                "fvg_score": trade["_fvg_score"],
                "close_reason": trade["close_reason"],
                "duration_min": trade["duration_min"],
                "net_pnl_usd": trade["_net_pnl"],
            }
            for trade in qualifying
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()