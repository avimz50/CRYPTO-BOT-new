"""Regression tests for daily risk reset, BTC trend guard, and opening-SL audit."""

import ast
from datetime import date, datetime
from pathlib import Path
import threading
import unittest


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
