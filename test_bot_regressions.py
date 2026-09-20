"""Regression tests for daily risk reset, BTC trend guard, and opening-SL audit."""

import ast
from datetime import date, datetime
from pathlib import Path
import re
import threading
import unittest

import pandas as pd


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def load_function(name, globals_dict):
    node = next(
        item for item in BOT_TREE.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    namespace = dict(globals_dict)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[name]


class TestDailyCircuitReset(unittest.TestCase):
    def _namespace(self, stored_date, pnl=-25.62, notified=True):
        return {
            "daily_stats": {
                "wins": 0,
                "losses": 1,
                "total_pnl": pnl,
                "date": stored_date,
                "close_reasons": {"SL": 1},
            },
            "_daily_circuit_notified": notified,
            "now_il": lambda: datetime(2026, 9, 16, 0, 1),
            "trades_lock": threading.Lock(),
        }

    def test_stale_loss_resets_without_active_trades(self):
        namespace = self._namespace(date(2026, 9, 15))
        reset = load_function("reset_daily_stats_if_new_day", namespace)

        self.assertTrue(reset())
        self.assertEqual(reset.__globals__["daily_stats"]["date"], date(2026, 9, 16))
        self.assertEqual(reset.__globals__["daily_stats"]["total_pnl"], 0.0)
        self.assertFalse(reset.__globals__["_daily_circuit_notified"])

    def test_same_day_is_not_reset(self):
        namespace = self._namespace(date(2026, 9, 16), pnl=-12.5)
        reset = load_function("reset_daily_stats_if_new_day", namespace)

        self.assertFalse(reset())
        self.assertEqual(reset.__globals__["daily_stats"]["total_pnl"], -12.5)

    def test_scan_loop_calls_reset_before_daily_report(self):
        scan_loop = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "scan_loop"
        )
        calls = [
            node.func.id
            for node in ast.walk(scan_loop)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("reset_daily_stats_if_new_day", calls)
        self.assertLess(
            calls.index("reset_daily_stats_if_new_day"),
            calls.index("check_daily_report"),
        )


class TestNeutralShortTrendGuard(unittest.TestCase):
    def test_neutral_short_uses_existing_strong_uptrend_helper(self):
        direction_gate = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "is_direction_allowed"
        )
        called_names = {
            node.func.id
            for node in ast.walk(direction_gate)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("is_btc_strong_uptrend", called_names)
        self.assertNotIn("check_btc_strong_uptrend", called_names)


class TestOpeningSlAudit(unittest.TestCase):
    def test_place_order_captures_opening_sl_before_append(self):
        place_order = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "place_order"
        )
        setdefault_calls = [
            node for node in ast.walk(place_order)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "sl_at_open"
        ]
        self.assertEqual(len(setdefault_calls), 1)

    def test_close_logger_never_uses_live_sl_as_opening_sl(self):
        close_logger = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "_log_closed_trade"
        )
        assignments = [
            node for node in ast.walk(close_logger)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "sl_at_open"
                    for target in node.targets)
        ]
        self.assertEqual(len(assignments), 1)
        value = assignments[0].value
        self.assertIsInstance(value, ast.Call)
        self.assertEqual(value.args[0].value, "sl_at_open")


class TestEntryAuditContext(unittest.TestCase):
    def setUp(self):
        self.normalize = load_function("_ensure_entry_audit_context", {"re": re})

    def test_swing_positive_signals_become_structured_fields(self):
        trade = {
            "score_breakdown": (
                "EMA_bypass=1(vol×4.2≥1.5) | Vol=30/30(×4.2) | FVG=+10(bullish)"
            )
        }
        self.normalize(trade)
        self.assertEqual(trade["volume_ratio"], 4.2)
        self.assertTrue(trade["ema_bypass"])
        self.assertTrue(trade["ema_bypass_evaluated"])
        self.assertEqual(trade["fvg_score"], 10)
        self.assertTrue(trade["fvg_evaluated"])
        self.assertNotIn("N/A", trade["score_breakdown"])

    def test_swing_zero_signals_remain_explicit(self):
        trade = {
            "score_breakdown": (
                "EMA_bypass=0(vol×1.7) | Vol=20/30(×1.7) | FVG=0(no gap)"
            )
        }
        self.normalize(trade)
        self.assertFalse(trade["ema_bypass"])
        self.assertEqual(trade["fvg_score"], 0)

    def test_legacy_swing_without_bypass_token_is_inferred_as_false(self):
        trade = {
            "score_breakdown": "Vol=20/30(×1.7) | FVG=0(no gap)"
        }
        self.normalize(trade)
        self.assertFalse(trade["ema_bypass"])
        self.assertTrue(trade["ema_bypass_evaluated"])
        self.assertNotIn("EMA_bypass=N/A", trade["score_breakdown"])

    def test_unscored_strategy_is_marked_not_evaluated(self):
        trade = {
            "score_breakdown": "Breakout Strategy LONG: 15m Vol×3.4",
            "volume_ratio": 3.37,
        }
        self.normalize(trade)
        self.assertEqual(trade["volume_ratio"], 3.37)
        self.assertIsNone(trade["ema_bypass"])
        self.assertFalse(trade["ema_bypass_evaluated"])
        self.assertIsNone(trade["fvg_score"])
        self.assertFalse(trade["fvg_evaluated"])
        self.assertIn("EMA_bypass=N/A(not evaluated)", trade["score_breakdown"])
        self.assertIn("FVG=N/A(not evaluated)", trade["score_breakdown"])
        first_breakdown = trade["score_breakdown"]
        self.normalize(trade)
        self.assertEqual(trade["score_breakdown"], first_breakdown)

    def test_closed_trade_audit_preserves_structured_context(self):
        close_logger = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "_log_closed_trade"
        )
        record = next(
            node.value for node in ast.walk(close_logger)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "record"
                    for target in node.targets)
            and isinstance(node.value, ast.Dict)
        )
        keys = {key.value for key in record.keys if isinstance(key, ast.Constant)}
        self.assertTrue({
            "volume_ratio", "ema_bypass", "ema_bypass_evaluated",
            "fvg_score", "fvg_evaluated",
        }.issubset(keys))


class TestBreakoutOpenAlert(unittest.TestCase):
    def test_alert_is_after_successful_place_order_guard(self):
        open_breakout = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "open_breakout_trade"
        )
        place_order_guard = next(
            node for node in ast.walk(open_breakout)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Call)
            and isinstance(node.test.operand.func, ast.Name)
            and node.test.operand.func.id == "place_order"
        )
        self.assertTrue(
            any(isinstance(node, ast.Return) and node.value.value is False
                for node in place_order_guard.body)
        )

        guarded_line = place_order_guard.lineno
        alert_line = next(
            node.lineno for node in ast.walk(open_breakout)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "send_chart_alert"
        )
        self.assertLess(guarded_line, alert_line)

    def test_breakout_uses_dedicated_tp1_and_chart_includes_tp2(self):
        open_breakout = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == "open_breakout_trade"
        )
        calc_call = next(
            node for node in ast.walk(open_breakout)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "calc_targets"
        )
        tp1_keyword = next(kw for kw in calc_call.keywords if kw.arg == "tp1_pct")
        self.assertEqual(tp1_keyword.value.id, "BREAKOUT_TP1_PCT")

        chart_call = next(
            node for node in ast.walk(open_breakout)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "generate_chart"
        )
        tp2_keyword = next(kw for kw in chart_call.keywords if kw.arg == "tp2")
        self.assertEqual(tp2_keyword.value.id, "tp_price")


class TestEarlyBreakoutEntry(unittest.TestCase):
    @staticmethod
    def _frame(close, high=None, low=None, volume=None):
        return pd.DataFrame({
            "close": close,
            "high": high if high is not None else close,
            "low": low if low is not None else close,
            "volume": volume if volume is not None else [100.0] * len(close),
        })

    def _check(self, price_15m, volume_15m=200.0, direction="LONG"):
        df_4h = self._frame([90.0 + i * 0.1 for i in range(25)])
        df_1h = self._frame(
            [99.0] * 15,
            high=[100.0] * 15,
            low=[98.0] * 15,
        )
        df_15m = self._frame(
            [99.5] * 14 + [price_15m],
            volume=[100.0] * 14 + [volume_15m],
        )
        frames = {"4h": df_4h, "1h": df_1h, "15m": df_15m}

        class FakeTa:
            @staticmethod
            def rsi(series, length):
                return pd.Series([55.0] * len(series))

        check = load_function("_coin_breakout_full", {
            "get_data": lambda symbol, timeframe, limit: frames[timeframe],
            "ta": FakeTa,
            "BREAKOUT_MIN_VOL": 1.5,
            "BREAKOUT_MAX_EXTENSION_PCT": 1.0,
        })
        return check("FET/USDT:USDT", direction)

    def test_long_uses_15m_confirmation_before_1h_close(self):
        signal, price, level, _, vol_ratio = self._check(100.5)
        self.assertTrue(signal)
        self.assertEqual(price, 100.5)
        self.assertEqual(level, 100.0)
        self.assertEqual(vol_ratio, 2.0)

    def test_long_rejects_late_chased_entry(self):
        signal, price, level, _, _ = self._check(102.0)
        self.assertFalse(signal)
        self.assertEqual(price, 102.0)
        self.assertEqual(level, 100.0)

    def test_long_still_requires_volume_confirmation(self):
        signal, _, _, _, vol_ratio = self._check(100.5, volume_15m=120.0)
        self.assertFalse(signal)
        self.assertEqual(vol_ratio, 1.2)
