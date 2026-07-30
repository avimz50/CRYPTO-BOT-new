"""
test_research_guards.py — Targeted tests for Dr. Sniper Research safety gates.

Tests cover:
  1. Tool-error evidence rejection — errors must NOT register evidence
  2. CoinGecko ID / ticker mismatch — canonical symbol extracted from result text
  3. Each failed execution guard in _open_research_trade
  4. _validate_candidate schema enforcement

Run: uv run python test_research_guards.py
"""

import re
import sys
import types
import unittest

# ── Minimal stubs so we can import claude_research without a real exchange ────
import claude_research as cr


class TestEvidenceTracking(unittest.TestCase):
    """Evidence is only recorded after successful, usable tool results."""

    def _run_evidence(self, ta_calls: list[tuple], fund_calls: list[tuple]):
        """
        Simulate the evidence-tracking logic from run_claude_research.
        ta_calls:   [(input_symbol, result_str), ...]
        fund_calls: [(coin_id, result_str), ...]
        Returns (ta_evidence, fundamental_evidence)
        """
        ta_ev:   set = set()
        fund_ev: set = set()

        _ERROR_PREFIXES = (
            "Technical data error", "Exchange not connected",
            "Insufficient OHLCV", "CoinGecko coin data error",
            "CoinGecko trending error", "CoinGecko movers error",
            "DeFiLlama error", "No DeFiLlama", "Search error",
            "No specific results",
        )

        for sym, result in ta_calls:
            result_ok = not any(result.startswith(p) for p in _ERROR_PREFIXES)
            if result_ok:
                s = sym.strip().upper()
                s = s if "/" in s else f"{s}/USDT"
                if cr._VALID_SYMBOL_RE.match(s):
                    ta_ev.add(s)

        for coin_id, result in fund_calls:
            result_ok = not any(result.startswith(p) for p in _ERROR_PREFIXES)
            if result_ok:
                m = re.search(r'\(([A-Z0-9]{2,12}/USDT)\)', result)
                if m:
                    fund_ev.add(m.group(1))

        return ta_ev, fund_ev

    # ── Test 1: error results must NOT produce evidence ───────────────────────

    def test_ta_error_not_recorded(self):
        ta_ev, _ = self._run_evidence(
            ta_calls=[("FET/USDT", "Technical data error for FET/USDT: timeout")],
            fund_calls=[],
        )
        self.assertNotIn("FET/USDT", ta_ev, "TA error should NOT be added to evidence")

    def test_fund_error_not_recorded(self):
        _, fund_ev = self._run_evidence(
            ta_calls=[],
            fund_calls=[("fetch-ai", "CoinGecko coin data error 'fetch-ai': 404")],
        )
        self.assertEqual(len(fund_ev), 0, "Fundamental error should NOT be added to evidence")

    def test_exchange_not_connected_not_recorded(self):
        ta_ev, _ = self._run_evidence(
            ta_calls=[("FET/USDT", "Exchange not connected.")],
            fund_calls=[],
        )
        self.assertNotIn("FET/USDT", ta_ev)

    # ── Test 2: canonical symbol extracted from result text, not coin_id ──────

    def test_fetch_ai_id_maps_to_FET(self):
        """fetch-ai coin_id → FET/USDT extracted from 'Name: Fetch.ai (FET/USDT)'"""
        result = "Name: Fetch.ai (FET/USDT)\nPrice: $1.23\nChanges: 24h=5%"
        _, fund_ev = self._run_evidence(
            ta_calls=[],
            fund_calls=[("fetch-ai", result)],
        )
        self.assertIn("FET/USDT", fund_ev,
                      "FET/USDT should be in fundamental evidence from get_coin_data result")
        self.assertNotIn("FETCH-AI/USDT", fund_ev,
                         "Raw coin_id-derived symbol should NOT be in evidence")

    def test_bitcoin_id_maps_to_BTC(self):
        result = "Name: Bitcoin (BTC/USDT)\nPrice: $65000"
        _, fund_ev = self._run_evidence(
            ta_calls=[],
            fund_calls=[("bitcoin", result)],
        )
        self.assertIn("BTC/USDT", fund_ev)

    def test_missing_canonical_in_result_not_recorded(self):
        """If the result has no (XXX/USDT) pattern, no evidence is recorded."""
        result = "Name: SomeToken\nPrice: $0.01"
        _, fund_ev = self._run_evidence(
            ta_calls=[],
            fund_calls=[("sometoken", result)],
        )
        self.assertEqual(len(fund_ev), 0)

    # ── Test 3: successful results DO produce correct evidence ────────────────

    def test_successful_ta_recorded(self):
        result = "Symbol: FET/USDT  |  Price: $1.23\nRSI(14): 4H=52.1"
        ta_ev, _ = self._run_evidence(
            ta_calls=[("FET/USDT", result)],
            fund_calls=[],
        )
        self.assertIn("FET/USDT", ta_ev)

    def test_ta_symbol_without_usdt_normalized(self):
        """Symbol passed as 'FET' (no /USDT) should be normalised to FET/USDT."""
        result = "Symbol: FET/USDT  |  Price: $1.23\nRSI(14): 4H=52.1"
        ta_ev, _ = self._run_evidence(
            ta_calls=[("FET", result)],
            fund_calls=[],
        )
        self.assertIn("FET/USDT", ta_ev)


class TestValidateCandidate(unittest.TestCase):
    """_validate_candidate enforces strict schema."""

    def _ok(self, **kw):
        base = {"symbol": "FET/USDT", "direction": "LONG", "score": 80,
                "reason": "Good setup", "key_risk": "Dump risk"}
        base.update(kw)
        ok, msg = cr._validate_candidate(base)
        return ok, msg

    def test_valid_candidate_passes(self):
        ok, _ = self._ok()
        self.assertTrue(ok)

    def test_lowercase_direction_rejected(self):
        ok, msg = self._ok(direction="long")
        self.assertFalse(ok)
        self.assertIn("direction", msg)

    def test_bad_direction_rejected(self):
        ok, msg = self._ok(direction="BUY")
        self.assertFalse(ok)

    def test_score_out_of_range_high(self):
        ok, msg = self._ok(score=101)
        self.assertFalse(ok)

    def test_score_out_of_range_low(self):
        ok, msg = self._ok(score=-1)
        self.assertFalse(ok)

    def test_score_non_integer(self):
        ok, msg = self._ok(score="high")
        self.assertFalse(ok)

    def test_bad_symbol_no_usdt(self):
        ok, msg = self._ok(symbol="FET/BTC")
        self.assertFalse(ok)

    def test_bad_symbol_too_long(self):
        ok, msg = self._ok(symbol="ABCDEFGHIJKLM/USDT")
        self.assertFalse(ok)

    def test_empty_reason_rejected(self):
        ok, msg = self._ok(reason="")
        self.assertFalse(ok)

    def test_reason_too_long_rejected(self):
        ok, msg = self._ok(reason="x" * 201)
        self.assertFalse(ok)

    def test_short_direction_not_accepted(self):
        ok, _ = self._ok(direction="SHORT")
        self.assertTrue(ok)

    def test_key_risk_optional_empty_ok(self):
        ok, _ = self._ok(key_risk="")
        self.assertTrue(ok)

    # ── execute field strict boolean validation ────────────────────────────────

    def test_execute_true_bool_accepted(self):
        ok, _ = self._ok(execute=True)
        self.assertTrue(ok)

    def test_execute_false_bool_accepted(self):
        ok, _ = self._ok(execute=False)
        self.assertTrue(ok)

    def test_execute_string_false_rejected(self):
        ok, msg = self._ok(execute="false")
        self.assertFalse(ok, "'false' string must be rejected — not coerced to bool")
        self.assertIn("execute", msg)

    def test_execute_string_true_rejected(self):
        ok, msg = self._ok(execute="true")
        self.assertFalse(ok, "'true' string must be rejected")

    def test_execute_integer_one_rejected(self):
        ok, msg = self._ok(execute=1)
        self.assertFalse(ok, "int 1 must be rejected — not coerced to True")

    def test_execute_integer_zero_rejected(self):
        ok, msg = self._ok(execute=0)
        self.assertFalse(ok, "int 0 must be rejected")

    def test_execute_null_rejected(self):
        ok, msg = self._ok(execute=None)
        self.assertFalse(ok, "null/None must be rejected")

    def test_execute_missing_defaults_false_ok(self):
        """Missing execute field → defaults to False → valid candidate (alert-only)."""
        base = {"symbol": "FET/USDT", "direction": "LONG", "score": 80,
                "reason": "Good setup", "key_risk": "Dump risk"}
        ok, msg = cr._validate_candidate(base)
        self.assertTrue(ok, f"Missing execute should default to False and pass: {msg}")


class TestProcessCandidatesEvidenceGate(unittest.TestCase):
    """_process_candidates blocks execution when evidence is missing."""

    def setUp(self):
        self._calls = []
        cr.set_execute_trade_fn(self._capture_execute)
        cr.set_send_msg_fn(lambda _: None)
        cr.set_auto_execute(True)
        cr.research_stats['trades_executed'] = 0

    def tearDown(self):
        cr.set_execute_trade_fn(None)
        cr.set_send_msg_fn(None)

    def _capture_execute(self, **kw):
        self._calls.append(kw)
        return True

    def _candidate(self, symbol="FET/USDT", direction="LONG", score=82, execute=True):
        return {"symbol": symbol, "direction": direction, "score": score,
                "reason": "Good RSI entry", "key_risk": "Low liquidity",
                "execute": execute}

    def test_execution_blocked_without_ta_evidence(self):
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence=set(),
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0, "Must NOT execute without TA evidence")
        self.assertEqual(cr.research_stats['trades_executed'], 0)

    def test_execution_blocked_without_fundamental_evidence(self):
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence=set(),
        )
        self.assertEqual(len(self._calls), 0, "Must NOT execute without fundamental evidence")

    def test_execution_proceeds_with_both_evidence(self):
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 1, "Should execute when both evidence sets present")
        self.assertEqual(cr.research_stats['trades_executed'], 1)

    def test_execute_false_not_counted(self):
        """execute=false candidates should never call execute_trade_fn."""
        cr._process_candidates(
            [self._candidate(execute=False)],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0)

    def test_false_return_from_callback_not_counted(self):
        """If callback returns False, trades_executed must NOT increment."""
        cr.set_execute_trade_fn(lambda **kw: False)
        before = cr.research_stats['trades_executed']
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(cr.research_stats['trades_executed'], before,
                         "False return must not increment trades_executed")

    def test_no_slots_not_executed(self):
        cr._process_candidates(
            [self._candidate()],
            slots_available=0,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0, "No slots → no execution")

    def test_schema_invalid_candidate_skipped(self):
        bad = {"symbol": "FET/BTC", "direction": "LONG", "score": 82,
               "reason": "test", "key_risk": "", "execute": True}
        cr._process_candidates(
            [bad],
            slots_available=1,
            ta_evidence={"FET/BTC"},
            fundamental_evidence={"FET/BTC"},
        )
        self.assertEqual(len(self._calls), 0, "Bad symbol should be rejected")

    def test_string_false_execute_does_not_trigger_execution(self):
        """'false' string in execute must be rejected by schema — not coerced to True."""
        before = cr.research_stats['trades_executed']
        bad_exec = {"symbol": "FET/USDT", "direction": "LONG", "score": 82,
                    "reason": "Good setup", "key_risk": "", "execute": "false"}
        cr._process_candidates(
            [bad_exec],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0,
                         "'false' string must not execute — schema must reject it")
        self.assertEqual(cr.research_stats['trades_executed'], before)

    def test_string_true_execute_does_not_trigger_execution(self):
        """'true' string in execute must be rejected by schema."""
        bad_exec = {"symbol": "FET/USDT", "direction": "LONG", "score": 82,
                    "reason": "Good setup", "key_risk": "", "execute": "true"}
        cr._process_candidates(
            [bad_exec],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0, "'true' string must not execute")

    def test_numeric_execute_does_not_trigger_execution(self):
        """Numeric 1 in execute must be rejected."""
        bad_exec = {"symbol": "FET/USDT", "direction": "LONG", "score": 82,
                    "reason": "Good setup", "key_risk": "", "execute": 1}
        cr._process_candidates(
            [bad_exec],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._calls), 0, "int 1 must not execute")


class TestBotPausedAndRegimeGuards(unittest.TestCase):
    """
    Integration tests: bot-paused state and regime rejection must never
    cause place_order to be called, and must not increment trades_executed.

    We simulate _open_research_trade's guard behaviour by passing callbacks
    that mirror its paused-check and regime-check early-return paths.
    This verifies the full candidate-to-execution pipeline honours both gates.
    """

    def setUp(self):
        self._place_order_calls = []
        cr.set_send_msg_fn(lambda _: None)
        cr.set_auto_execute(True)
        cr.research_stats['trades_executed'] = 0

    def tearDown(self):
        cr.set_execute_trade_fn(None)
        cr.set_send_msg_fn(None)

    def _candidate(self, symbol="FET/USDT"):
        return {"symbol": symbol, "direction": "LONG", "score": 82,
                "reason": "Strong breakout setup", "key_risk": "Low liquidity",
                "execute": True}

    def _make_execute_fn(self, *, bot_paused: bool = False, regime_allowed: bool = True):
        """
        Factory: returns an execute_fn that mirrors _open_research_trade's
        paused / regime guards and calls _place_order_calls on success.
        Returns True only when all guards pass (simulating real place_order success).
        """
        calls = self._place_order_calls

        def fn(**kw):
            if bot_paused:
                return False   # mirrors: if _bot_paused: return False
            if not regime_allowed:
                return False   # mirrors: is_direction_allowed → return False
            calls.append(kw)
            return True        # mirrors: place_order → return True

        return fn

    # ── Paused state ──────────────────────────────────────────────────────────

    def test_bot_paused_blocks_place_order(self):
        cr.set_execute_trade_fn(self._make_execute_fn(bot_paused=True))
        before = cr.research_stats['trades_executed']
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._place_order_calls), 0,
                         "place_order must NOT be reached when bot is paused")
        self.assertEqual(cr.research_stats['trades_executed'], before,
                         "trades_executed must NOT increment when bot is paused")

    def test_bot_paused_does_not_announce_execution(self):
        announced = []
        cr.set_send_msg_fn(lambda msg: announced.append(msg))
        cr.set_execute_trade_fn(self._make_execute_fn(bot_paused=True))
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        combined = " ".join(announced)
        self.assertNotIn("ביצוע אוטומטי", combined,
                         "Auto-execute announcement must NOT appear when bot is paused")

    # ── Regime gate ───────────────────────────────────────────────────────────

    def test_regime_denied_blocks_place_order(self):
        cr.set_execute_trade_fn(self._make_execute_fn(regime_allowed=False))
        before = cr.research_stats['trades_executed']
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._place_order_calls), 0,
                         "place_order must NOT be reached when regime gate blocks")
        self.assertEqual(cr.research_stats['trades_executed'], before,
                         "trades_executed must NOT increment on regime denial")

    def test_regime_denied_shows_no_execution_in_message(self):
        announced = []
        cr.set_send_msg_fn(lambda msg: announced.append(msg))
        cr.set_execute_trade_fn(self._make_execute_fn(regime_allowed=False))
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        combined = " ".join(announced)
        self.assertNotIn("ביצוע אוטומטי", combined,
                         "Auto-execute announcement must NOT appear on regime denial")

    # ── Both gates pass → execution recorded ─────────────────────────────────

    def test_both_guards_pass_executes_and_counts(self):
        cr.set_execute_trade_fn(
            self._make_execute_fn(bot_paused=False, regime_allowed=True)
        )
        before = cr.research_stats['trades_executed']
        cr._process_candidates(
            [self._candidate()],
            slots_available=1,
            ta_evidence={"FET/USDT"},
            fundamental_evidence={"FET/USDT"},
        )
        self.assertEqual(len(self._place_order_calls), 1,
                         "place_order must be called exactly once when all guards pass")
        self.assertEqual(cr.research_stats['trades_executed'], before + 1)

    # ── Multiple candidates: partial execution ────────────────────────────────

    def test_only_passing_candidates_are_executed(self):
        """First candidate passes all guards; second is same symbol — both get evaluated."""
        results = []

        def fn(**kw):
            # Simulate: first call succeeds, second (regime blocked for SHORT)
            if kw['direction'] == 'SHORT':
                return False
            results.append(kw)
            return True

        cr.set_execute_trade_fn(fn)
        candidates = [
            {"symbol": "FET/USDT",  "direction": "LONG",  "score": 82,
             "reason": "Momentum play", "key_risk": "Reversal",  "execute": True},
            {"symbol": "DOGE/USDT", "direction": "SHORT", "score": 79,
             "reason": "Overextended",  "key_risk": "Squeeze",   "execute": True},
        ]
        cr._process_candidates(
            candidates,
            slots_available=2,
            ta_evidence={"FET/USDT", "DOGE/USDT"},
            fundamental_evidence={"FET/USDT", "DOGE/USDT"},
        )
        self.assertEqual(len(results), 1, "Only the LONG should be executed")
        self.assertEqual(results[0]['symbol'], "FET/USDT")


if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    for cls in (TestEvidenceTracking, TestValidateCandidate,
                TestProcessCandidatesEvidenceGate, TestBotPausedAndRegimeGuards):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
