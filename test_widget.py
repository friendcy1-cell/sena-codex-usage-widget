import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from usage_core import (UsageError, QuotaClient, normalize, headline, window_name,
                        pct, load_history, save_history, change_since, credit_amount)
from widget import read_preferences, countdown, Widget


def raw(used=38):
    return {"accountId": "private-account", "rateLimitsByLimitId": {
        "codex": {"planType": "pro", "primary": {
            "usedPercent": used, "windowDurationMins": 10080, "resetsAt": 9000},
            "credits": {"balance": "123.456"}},
        "spark": {"primary": {"usedPercent": 0}}},
        "rateLimitResetCredits": {"availableCount": 2, "credits": [
            {"id": "secret-id", "status": "available", "expiresAt": 10000},
            {"status": "available", "expiresAt": 2000}]}}


class QuotaTests(unittest.TestCase):
    def test_remaining_not_used(self):
        self.assertEqual(headline(normalize(raw(), 3000))["remaining"], 62)

    def test_no_raw_ids_or_credentials(self):
        text = json.dumps(normalize(raw(), 3000))
        for secret in ("private-account", "secret-id", "access_token", "email"):
            self.assertNotIn(secret, text)

    def test_credit_balance_preserves_decimal_and_formats_without_rounding_up(self):
        s = normalize(raw(), 3000)
        self.assertEqual(s["credits"]["balance"], "123.456")
        self.assertEqual(credit_amount(s), "123.45")
        r = raw()
        r["rateLimitsByLimitId"]["codex"]["credits"]["balance"] = "1234.5603510000"
        self.assertEqual(credit_amount(normalize(r, 3000)), "1,234.56")
        r["rateLimitsByLimitId"]["codex"]["credits"]["balance"] = "0.0001"
        self.assertEqual(credit_amount(normalize(r, 3000)), "<0.01")

    def test_missing_or_invalid_credits_never_become_zero(self):
        for balance in (None, "", "bad", "NaN", "Infinity", "-1", True, "1e9999999", "1e-9999999"):
            r = raw()
            r["rateLimitsByLimitId"]["codex"]["credits"]["balance"] = balance
            with self.subTest(balance=balance):
                self.assertIsNone(normalize(r, 3000)["credits"])
                self.assertEqual(credit_amount(normalize(r, 3000)), "미제공")
        r["rateLimitsByLimitId"]["codex"].pop("credits")
        self.assertEqual(credit_amount(normalize(r, 3000)), "미제공")

    def test_real_zero_and_unlimited_credits_are_distinct(self):
        r = raw()
        r["rateLimitsByLimitId"]["codex"]["credits"] = {"balance": "0.0000"}
        self.assertEqual(credit_amount(normalize(r, 3000)), "0")
        r["rateLimitsByLimitId"]["codex"]["credits"] = {"balance": None, "unlimited": True}
        self.assertEqual(credit_amount(normalize(r, 3000)), "무제한")

    def test_credits_do_not_replace_or_raise_remaining_quota(self):
        for used in (0, 80, 100):
            r = raw(used)
            r["rateLimitsByLimitId"]["codex"]["credits"]["balance"] = "1234.56"
            self.assertEqual(headline(normalize(r, 3000))["remaining"], 100 - used)

    def test_float_if_provider_supplies_it(self):
        self.assertEqual(pct(headline(normalize(raw(38.25), 3000))["remaining"]), "61.75")
        self.assertEqual(pct(62), "62")

    def test_zero_and_full(self):
        self.assertEqual(headline(normalize(raw(100), 3000))["remaining"], 0)
        self.assertEqual(headline(normalize(raw(0), 3000))["remaining"], 100)

    def test_bad_numbers_fail_closed(self):
        for val in (None, True, -1, 101, "38", float("nan"), float("inf")):
            with self.subTest(val=val), self.assertRaises(UsageError):
                normalize(raw(val), 3000)

    def test_no_spark_substitution(self):
        r = raw()
        del r["rateLimitsByLimitId"]["codex"]
        with self.assertRaises(UsageError):
            normalize(r, 3000)

    def test_legacy_fallback(self):
        r = {"rateLimits": raw()["rateLimitsByLimitId"]["codex"]}
        self.assertEqual(headline(normalize(r, 3000))["remaining"], 62)

    def test_second_window_is_limiting(self):
        r = raw()
        r["rateLimitsByLimitId"]["codex"]["secondary"] = {
            "usedPercent": 90, "windowDurationMins": 300, "resetsAt": 4000}
        s = normalize(r, 3000)
        self.assertEqual(headline(s)["remaining"], 10)
        self.assertEqual(window_name(headline(s)), "5시간")

    def test_coupons_nullable(self):
        r = raw()
        r["rateLimitResetCredits"] = None
        self.assertIsNone(normalize(r, 3000)["coupons"])

    def test_expired_coupon_filtered(self):
        s = normalize(raw(), 3000)
        self.assertEqual(s["coupon_expiry"], 10000)
        self.assertEqual(s["coupons"], 2)

    def test_read_only_rpc_source(self):
        src = Path("usage_core.py").read_text(encoding="utf-8")
        for banned in ("turn/start", "thread/start", "thread/resume", "consume", "auth.json"):
            self.assertNotIn(banned, src)

    def test_cancel_does_not_spawn(self):
        client = QuotaClient()
        client.close()
        with patch("usage_core.subprocess.Popen") as popen, self.assertRaises(UsageError):
            client.fetch()
        popen.assert_not_called()


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.a = normalize(raw(38), 3000)
        self.b = normalize(raw(40), 3200)

    def test_observed_percent_points(self):
        c = change_since([self.a], self.b, 2500)
        self.assertEqual(c["drop"], 2)
        self.assertEqual(c["start"], 3000)

    def test_unknown_history_not_zero(self):
        self.assertIsNone(change_since([], self.b, 2500))

    def test_other_accounts_not_combined(self):
        self.a["account"] = "another"
        self.assertIsNone(change_since([self.a], self.b, 2500))

    def test_other_plans_not_combined(self):
        self.a["plan"] = "plus"
        self.assertIsNone(change_since([self.a], self.b, 2500))

    def test_reset_timestamp_change_is_hold(self):
        self.b["windows"][0]["reset"] = 9999
        self.assertTrue(change_since([self.a], self.b, 2500)["reset"])

    def test_recovery_even_without_reset_change_is_hold(self):
        self.b["windows"][0]["remaining"] = 100
        self.assertTrue(change_since([self.a], self.b, 2500)["reset"])

    def test_intermediate_reset_is_not_hidden(self):
        mid = copy.deepcopy(self.a)
        mid["at"] = 3100
        mid["windows"][0]["remaining"] = 100
        self.assertTrue(change_since([self.a, mid], self.b, 2500)["reset"])

    def test_gap_label(self):
        self.b["at"] = 5000
        self.assertTrue(change_since([self.a], self.b, 2500)["gap"])

    def test_roundtrip_and_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            old = copy.deepcopy(self.a)
            old["at"] = time.time() - 10 * 86400
            self.a["at"] = time.time()
            rows = [old, self.a]
            save_history(path, rows)
            self.assertEqual(len(load_history(path)), 1)
            self.assertNotIn("credits", path.read_text(encoding="utf-8"))
            self.assertNotIn("123.456", path.read_text(encoding="utf-8"))
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(load_history(path), [])

    def test_unknown_file_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text('{"bad":true}', encoding="utf-8")
            self.assertEqual(load_history(path), [])

    def test_corrupt_windows_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            for windows in ([{}], [None], [{"slot": "primary", "remaining": "bad"}]):
                bad = copy.deepcopy(self.a)
                bad["windows"] = windows
                path.write_text(json.dumps([bad]), encoding="utf-8")
                self.assertEqual(load_history(path), [])

    def test_duplicate_windows_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            bad = copy.deepcopy(self.a)
            bad["windows"] *= 2
            path.write_text(json.dumps([bad]), encoding="utf-8")
            self.assertEqual(load_history(path), [])


class PresentationTests(unittest.TestCase):
    def draw_widget(self, remaining, balance="1234.56", fresh=True):
        w = Widget.__new__(Widget)
        w.canvas = MagicMock()
        w.images = {key: key for key in ("idle_alt", "fail", "success", "frame_1")}
        w.snapshot = normalize(raw(100 - remaining), time.time())
        w.snapshot["credits"] = {"balance": balance, "unlimited": False}
        w.prefs, w.busy, w.preview = {"animation": False}, False, False
        w.success_until = 0
        w.is_fresh = lambda: fresh
        w.draw()
        return w

    def test_credit_display_keeps_existing_low_and_zero_quota_poses(self):
        for remaining in (20, 0):
            for balance in ("1234.56", "0"):
                with self.subTest(remaining=remaining, balance=balance):
                    w = self.draw_widget(remaining, balance)
                    self.assertEqual(w.canvas.create_image.call_args.kwargs["image"], "fail")
        self.assertEqual(self.draw_widget(21).canvas.create_image.call_args.kwargs["image"], "idle_alt")

    def test_compact_credit_display_and_stale_label(self):
        w = self.draw_widget(20)
        texts = [c.kwargs.get("text") for c in w.canvas.create_text.call_args_list]
        self.assertIn("20%", texts)
        self.assertIn("1,234.56", texts)
        self.assertIn("크레딧 남음", texts)
        stale = self.draw_widget(20, fresh=False)
        self.assertIn("이전 크레딧", [c.kwargs.get("text") for c in stale.canvas.create_text.call_args_list])

    def test_credit_line_is_below_percent(self):
        calls = self.draw_widget(20).canvas.create_text.call_args_list
        percent = next(c for c in calls if c.kwargs.get("text") == "20%")
        credit = next(c for c in calls if c.kwargs.get("text") == "1,234.56")
        self.assertGreater(credit.args[1], percent.args[1])

    def test_details_credit_line_and_freshness(self):
        w = self.draw_widget(20)
        w.panel = MagicMock()
        w.big, w.credit_label, w.status_label, w.info, w.trend = (MagicMock() for _ in range(5))
        w.error, w.storage_error, w.history = "", "", []
        w.update_details()
        self.assertEqual(w.credit_label.configure.call_args.kwargs['text'], '크레딧  1,234.56 남음')
        w.is_fresh = lambda: False
        w.update_details()
        self.assertIn('마지막 확인값', w.credit_label.configure.call_args.kwargs['text'])
        w.snapshot['credits'] = None
        w.update_details()
        self.assertEqual(w.credit_label.configure.call_args.kwargs['text'], '크레딧 잔액 미제공')

    def test_cached_snapshot_is_not_live(self):
        w = Widget.__new__(Widget)
        w.snapshot = normalize(raw(), time.time())
        w.snapshot["windows"][0]["reset"] = time.time() + 1000
        w.verified_this_run, w.error, w.prefs = False, "", {"interval": 120}
        self.assertFalse(w.is_fresh())
        w.verified_this_run = True
        self.assertTrue(w.is_fresh())
        w.error = "offline"
        self.assertFalse(w.is_fresh())

    def test_old_or_reset_passed_is_stale(self):
        w = Widget.__new__(Widget)
        w.snapshot = normalize(raw(), time.time() - 1000)
        w.snapshot["windows"][0]["reset"] = time.time() + 1000
        w.verified_this_run, w.error, w.prefs = True, "", {"interval": 120}
        self.assertFalse(w.is_fresh())
        w.snapshot["at"] = time.time()
        w.snapshot["windows"][0]["reset"] = time.time() - 1
        self.assertFalse(w.is_fresh())

    def test_read_preferences_no_unsafe_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preferences.json"
            path.write_text('{"interval":1,"alerts":"yes","topmost":false}', encoding="utf-8")
            p = read_preferences(path)
            self.assertEqual(p["interval"], 120)
            self.assertTrue(p["alerts"])
            self.assertFalse(p["topmost"])

    def test_countdown_does_not_predict_replenishment(self):
        self.assertIn("경과", countdown(1000, 2000))
        self.assertEqual(countdown(None, 2000), "시간 미제공")


if __name__ == "__main__":
    unittest.main(verbosity=2)
