import json
import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B


class CreateValidation(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()

    def rejected(self, **over):
        with self.assertRaises(B.PaymentRejected) as ctx:
            B.create_sla(self.c, **over)
        return str(ctx.exception)

    def test_create_returns_sequential_ids_and_locks_fee(self):
        self.assertEqual(B.create_sla(self.c), "sla_0")
        self.assertEqual(B.create_sla(self.c), "sla_1")
        self.assertEqual(int(self.c.locked_total), 2 * B.TOTAL)
        self.assertEqual(B.accounting(self.c)["solvent"], True)

    def test_anchors_are_recorded(self):
        sla = B.get(self.c, B.create_sla(self.c))
        self.assertEqual(sla["status"], "open")
        self.assertEqual(sla["customer_key"], B.CUSTOMER)
        self.assertEqual(sla["provider_key"], B.PROVIDER)
        self.assertEqual(sla["health_url"], B.URL)
        self.assertEqual(sla["fee_per_epoch"], str(B.FEE_PER_EPOCH))
        self.assertEqual(sla["bond_amount"], str(B.BOND))
        self.assertEqual(sla["tag"], B.tag_for("sla_0"))
        self.assertEqual(sla["check"], {"field": "status", "expected": "ok"})
        self.assertEqual(len(sla["epochs"]), B.EPOCHS)

    def test_fee_must_split_evenly(self):
        self.assertIn("split evenly", self.rejected(value=B.TOTAL + 1))

    def test_zero_value_rejected_without_gen(self):
        B.set_caller(B.CUSTOMER)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.create_sla(*[B.default_args()[k] for k in B.ARG_ORDER])

    def test_provider_must_differ_and_be_valid(self):
        self.assertIn("different", self.rejected(provider_wallet=B.CUSTOMER))
        self.assertIn("valid address", self.rejected(provider_wallet="nope"))

    def test_name_required_and_bounded(self):
        self.assertIn("service_name", self.rejected(service_name="   "))
        self.assertIn("service_name", self.rejected(service_name="x" * 81))

    def test_bad_urls(self):
        bad = [
            "http://status.acme.dev/h", "https://user:pw@status.acme.dev/h", "https://status.acme.dev:8443/h",
            "https://localhost/h", "https://127.0.0.1/h", "https://10.0.0.5/h", "https://intranet.local/h",
            "https://status.acme.dev/h#frag", "https://status.acme.dev/a b", "ftp://status.acme.dev/h",
            "https://nodots/h", "", "https://" + "a" * 300 + ".dev/h", "https://[::1]/h",
            "https://svc.internal/h", "https://x.example/h", "https://status.acme.dev\\evil/h",
        ]
        for url in bad:
            with self.subTest(url=url):
                self.assertIn("health_url", self.rejected(health_url=url))

    def test_good_urls_are_canonicalised(self):
        sla = B.get(self.c, B.create_sla(self.c, health_url="HTTPS://Status.Acme.DEV:443/Health?x=1"))
        self.assertEqual(sla["health_url"], "https://status.acme.dev/Health?x=1")

    def test_check_modes(self):
        self.assertIn("check_mode", self.rejected(check_mode="ping"))
        self.assertIn("check_spec", self.rejected(check_mode="tag", check_spec="status=ok"))
        self.assertIn("check_spec", self.rejected(check_mode="json", check_spec="status"))
        self.assertIn("check_spec", self.rejected(check_mode="json", check_spec="status=ok;age<=0"))
        self.assertIn("check_spec", self.rejected(check_mode="semantic", check_spec="short"))
        self.assertIn("check_spec", self.rejected(check_mode="semantic", check_spec="x" * 601))
        B.create_sla(self.c, check_mode="tag", check_spec="")
        B.create_sla(self.c, check_mode="json", check_spec="status=ok;updated_at<=300")
        B.create_sla(self.c, check_mode="semantic", check_spec="The page lists at least three plans with prices")

    def test_numeric_bounds(self):
        cases = [
            ("epoch_seconds", B.m.MIN_EPOCH_SECONDS - 1), ("epoch_seconds", 31 * 86400 + 1), ("epochs", 0), ("epochs", 25),
            ("bond_bps", 999), ("bond_bps", 20001), ("penalty_bps", -1), ("penalty_bps", 10001),
            ("outage_probes", 1), ("outage_probes", 11), ("min_gap_seconds", B.m.MIN_GAP_SECONDS - 1), ("min_gap_seconds", 6 * 3600 + 1),
            ("window_seconds", 599), ("window_seconds", 86401), ("max_breaches", 0), ("max_breaches", 4),
            ("cache_bust", 2),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.assertIn(field, self.rejected(**{field: value}))

    def test_non_integer_numbers_rejected(self):
        for field in ("epoch_seconds", "bond_bps", "outage_probes", "min_gap_seconds", "window_seconds"):
            for bad in (True, 1.5, "10"):
                with self.subTest(field=field, bad=bad):
                    self.assertIn(field, self.rejected(**{field: bad}))

    def test_window_must_fit_the_probes(self):
        self.assertIn("window_seconds", self.rejected(window_seconds=599))
        B.create_sla(self.c, window_seconds=600)

    def test_status_feed_rules(self):
        self.assertIn("status_feed", self.rejected(status_feed="https://evil.example.com/api/v2/incidents.json"))
        self.assertIn("status_feed", self.rejected(status_feed="https://www.githubstatus.com/api/v2/summary.json"))
        self.assertIn("status_feed", self.rejected(status_feed="https://www.githubstatus.com/api/v2/incidents.json?x=1"))
        self.assertIn("feed_component", self.rejected(feed_component="Actions"))
        self.assertIn("feed_component", self.rejected(status_feed="https://www.githubstatus.com/api/v2/incidents.json",
                                                      feed_component="x" * 61))
        sla = B.get(self.c, B.create_sla(self.c, status_feed="https://WWW.githubstatus.com/api/v2/incidents.json",
                                         feed_component="  Actions "))
        self.assertEqual(sla["status_feed"], "https://www.githubstatus.com/api/v2/incidents.json")
        self.assertEqual(sla["feed_component"], "Actions")

    def test_rejected_payment_is_refunded_not_stranded(self):
        before = B.accounting(self.c)
        self.rejected(health_url="http://bad")
        after = B.accounting(self.c)
        self.assertTrue(after["solvent"])
        self.assertEqual(int(after["owed"]) - int(before["owed"]), B.TOTAL)
        self.assertEqual(self.c.get_stats(), json.dumps({"cancelled": 0, "ended": 0, "live": 0, "total": 0}, sort_keys=True))

    def test_minimums(self):
        B.Keepalive.MIN_EPOCH_FEE = B.u256(10)
        try:
            self.assertIn("epoch fee", self.rejected(value=9 * B.EPOCHS))
        finally:
            B.Keepalive.MIN_EPOCH_FEE = B.u256(1)
        B.Keepalive.MIN_BOND = B.u256(10 ** 6)
        try:
            self.assertIn("bond", self.rejected())
        finally:
            B.Keepalive.MIN_BOND = B.u256(1)


class AcceptAndCancel(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.create_sla(self.c)

    def test_only_provider_with_exact_bond(self):
        B.set_caller(B.STRANGER)
        with self.assertRaises(B.PaymentRejected) as ctx:
            B.call_payable(self.c, "accept_sla", B.BOND, self.sla_id)
        self.assertIn("designated provider", str(ctx.exception))
        for wrong in (B.BOND - 1, B.BOND + 1, 0):
            with self.subTest(wrong=wrong):
                B.set_caller(B.PROVIDER)
                if wrong == 0:
                    with self.assertRaises(B.gl.vm.UserError):
                        B.call_payable(self.c, "accept_sla", 0, self.sla_id)
                else:
                    with self.assertRaises(B.PaymentRejected):
                        B.call_payable(self.c, "accept_sla", wrong, self.sla_id)

    def test_accept_starts_clock_after_warmup(self):
        B.accept_sla(self.c, self.sla_id)
        sla = B.get(self.c, self.sla_id)
        self.assertEqual(sla["status"], "active")
        self.assertEqual(sla["start_epoch"], B.BASE_EPOCH + B.WARMUP)
        self.assertEqual(sla["end_epoch"], sla["start_epoch"] + B.EPOCHS * B.EPOCH_SECONDS)
        self.assertEqual(sla["bond_left"], str(B.BOND))
        self.assertEqual(B.accounting(self.c)["locked"], str(B.TOTAL + B.BOND))

    def test_cannot_accept_twice(self):
        B.accept_sla(self.c, self.sla_id)
        with self.assertRaises(B.PaymentRejected):
            B.accept_sla(self.c, self.sla_id)

    def test_accept_unknown_agreement(self):
        with self.assertRaises(B.PaymentRejected):
            B.accept_sla(self.c, "sla_99", bond=5)

    def test_cancel_refunds_fee_to_customer_only(self):
        B.set_caller(B.STRANGER)
        B.expect_user_error(self, self.c.cancel_sla, self.sla_id)
        B.set_caller(B.PROVIDER)
        B.expect_user_error(self, self.c.cancel_sla, self.sla_id)
        B.set_caller(B.CUSTOMER)
        self.c.cancel_sla(self.sla_id)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.TOTAL)
        self.assertEqual(B.get(self.c, self.sla_id)["status"], "cancelled")
        self.assertEqual(B.accounting(self.c)["locked"], "0")
        B.expect_user_error(self, self.c.cancel_sla, self.sla_id)

    def test_cannot_cancel_after_accept(self):
        B.accept_sla(self.c, self.sla_id)
        B.set_caller(B.CUSTOMER)
        B.expect_user_error(self, self.c.cancel_sla, self.sla_id)

    def test_cannot_accept_after_cancel(self):
        B.set_caller(B.CUSTOMER)
        self.c.cancel_sla(self.sla_id)
        with self.assertRaises(B.PaymentRejected):
            B.accept_sla(self.c, self.sla_id)

    def test_withdraw_after_cancel(self):
        B.set_caller(B.CUSTOMER)
        self.c.cancel_sla(self.sla_id)
        self.assertEqual(B.withdraw(self.c, B.CUSTOMER), B.TOTAL)
        self.assertEqual(int(self.c.balance), 0)
        B.set_caller(B.CUSTOMER)
        B.expect_user_error(self, self.c.withdraw)


if __name__ == "__main__":
    unittest.main()
