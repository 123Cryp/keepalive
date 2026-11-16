import json
import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B


class Profile(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()

    def profile(self, address):
        return json.loads(self.c.get_profile(address))

    def listed(self, address, offset=0, limit=10):
        return json.loads(self.c.list_slas_for(address, offset, limit))

    def test_unknown_address_is_empty(self):
        p = self.profile(B.STRANGER)
        self.assertEqual(p["as_provider"]["agreements"], 0)
        self.assertEqual(p["as_customer"]["agreements"], 0)
        self.assertIsNone(p["as_provider"]["uptime_bps"])
        self.assertEqual(p["pending_withdrawal"], "0")
        self.assertEqual(self.listed(B.STRANGER)["slas"], [])

    def test_index_covers_both_parties_newest_first(self):
        B.create_sla(self.c)
        B.create_sla(self.c)
        B.create_sla(self.c, customer=B.STRANGER)
        mine = self.listed(B.CUSTOMER)
        self.assertEqual([r["sla_id"] for r in mine["slas"]], ["sla_1", "sla_0"])
        self.assertEqual(mine["total"], 2)
        theirs = self.listed(B.PROVIDER)
        self.assertEqual([r["sla_id"] for r in theirs["slas"]], ["sla_2", "sla_1", "sla_0"])
        self.assertEqual([r["sla_id"] for r in self.listed(B.PROVIDER, 1, 1)["slas"]], ["sla_1"])
        self.assertEqual(self.listed(B.PROVIDER, 9, 1)["slas"], [])

    def test_address_case_does_not_matter(self):
        B.create_sla(self.c)
        self.assertEqual(self.listed(B.PROVIDER.upper().replace("0X", "0x"))["total"], 1)

    def test_limit_is_clamped(self):
        for _ in range(3):
            B.create_sla(self.c)
        self.assertEqual(len(self.listed(B.CUSTOMER, 0, 0)["slas"]), 1)
        self.assertEqual(self.listed(B.CUSTOMER, 0, 999)["limit"], self.c.MAX_LIST_LIMIT)

    def test_rejected_create_is_not_indexed(self):
        with self.assertRaises(B.PaymentRejected):
            B.create_sla(self.c, epochs=0)
        self.assertEqual(self.listed(B.CUSTOMER)["total"], 0)

    def test_record_after_a_full_run_with_one_breach(self):
        sla_id = B.active_sla(self.c)
        B.prove_outage(self.c, sla_id, epoch=1, caller=B.CUSTOMER)
        B.run_to_end(self.c, sla_id)
        prov = self.profile(B.PROVIDER)["as_provider"]
        self.assertEqual((prov["clean"], prov["breached"], prov["excused"]), (2, 1, 0))
        self.assertEqual(prov["penalties_paid"], str(B.PENALTY))
        self.assertEqual(prov["fees_earned"], str(2 * B.FEE_PER_EPOCH))
        self.assertEqual(prov["uptime_bps"], 2 * 10000 // 3)
        self.assertEqual((prov["agreements"], prov["live"]), (1, 0))
        cust = self.profile(B.CUSTOMER)
        self.assertEqual(cust["as_customer"]["fees_paid"], str(2 * B.FEE_PER_EPOCH))
        self.assertEqual(cust["as_customer"]["penalties_received"], str(B.PENALTY))
        self.assertEqual(cust["pending_withdrawal"], str(B.pending(self.c, B.CUSTOMER)))
        self.assertEqual(cust["as_provider"]["agreements"], 0)

    def test_live_counts_open_and_active_but_not_cancelled(self):
        B.create_sla(self.c)
        self.assertEqual(self.profile(B.PROVIDER)["as_provider"]["live"], 1)
        B.set_caller(B.CUSTOMER)
        self.c.cancel_sla("sla_0")
        self.assertEqual(self.profile(B.PROVIDER)["as_provider"]["live"], 0)
        self.assertEqual(self.profile(B.PROVIDER)["as_provider"]["agreements"], 1)

    def test_views_do_not_change_state(self):
        sla_id = B.active_sla(self.c)
        before = (dict(self.c.slas), dict(self.c.party_index), B.accounting(self.c))
        self.c.get_profile(B.PROVIDER)
        self.c.list_slas_for(B.PROVIDER, 0, 5)
        self.c.get_sla_status(sla_id)
        self.assertEqual(before, (dict(self.c.slas), dict(self.c.party_index), B.accounting(self.c)))


class Status(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)

    def status(self):
        return json.loads(self.c.get_sla_status(self.sla_id))

    def test_unknown_id_fails(self):
        B.expect_user_error(self, self.c.get_sla_status, "sla_99")

    def test_open_agreement_has_no_clock(self):
        other = B.create_sla(self.c)
        s = json.loads(self.c.get_sla_status(other))
        self.assertEqual(s["status"], "open")
        self.assertIsNone(s["current_epoch"])
        self.assertIsNone(s["next_settle_in"])
        self.assertFalse(s["settle_ready"])

    def test_running_epoch_and_countdown(self):
        B.CLOCK.set_epoch(B.start_of(self.c, self.sla_id) + 100)
        s = self.status()
        self.assertEqual(s["current_epoch"], 0)
        self.assertEqual(s["current_epoch_ends_in"], B.EPOCH_SECONDS - 100)
        self.assertEqual(s["next_settle_epoch"], 0)
        self.assertEqual(s["next_settle_in"], B.EPOCH_SECONDS - 100 + B.SETTLE_DELAY)
        self.assertFalse(s["settle_ready"])
        self.assertFalse(s["can_terminate"])

    def test_settle_ready_after_the_delay(self):
        B.settle_time(self.c, self.sla_id, 0)
        s = self.status()
        self.assertTrue(s["settle_ready"])
        self.assertEqual(s["next_settle_in"], 0)

    def test_counted_downs_follow_the_window(self):
        B.CLOCK.set_epoch(B.start_of(self.c, self.sla_id) + B.WARMUP + 10)
        B.probe_down(self.c, self.sla_id)
        self.assertEqual(self.status()["current_counted_downs"], 1)
        B.CLOCK.advance(B.WINDOW + 1)
        self.assertEqual(self.status()["current_counted_downs"], 0)

    def test_can_terminate_after_enough_breaches(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.CUSTOMER)
        B.prove_outage(self.c, self.sla_id, epoch=1, caller=B.CUSTOMER)
        B.CLOCK.set_epoch(B.start_of(self.c, self.sla_id) + 2 * B.EPOCH_SECONDS + 5)
        B.settle_time(self.c, self.sla_id, 1)
        B.settle_due(self.c, self.sla_id)
        s = self.status()
        self.assertTrue(s["can_terminate"])
        self.assertEqual(s["breaches"], 2)

    def test_finished_agreement_has_no_countdown(self):
        B.run_to_end(self.c, self.sla_id)
        s = self.status()
        self.assertEqual(s["status"], "ended")
        self.assertIsNone(s["next_settle_in"])
        self.assertIsNone(s["current_epoch"])
        self.assertFalse(s["can_terminate"])


if __name__ == "__main__":
    unittest.main()
