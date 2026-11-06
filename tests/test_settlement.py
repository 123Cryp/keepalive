import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B


class Settlement(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)

    def check_solvent(self):
        acc = B.accounting(self.c)
        self.assertTrue(acc["solvent"], acc)

    def test_nothing_to_settle_before_the_delay(self):
        B.CLOCK.set_epoch(B.start_of(self.c, self.sla_id) + B.EPOCH_SECONDS + B.SETTLE_DELAY - 1)
        B.set_caller(B.STRANGER)
        B.expect_user_error(self, self.c.settle_due, self.sla_id)

    def test_clean_epoch_pays_the_provider(self):
        B.settle_time(self.c, self.sla_id, 0)
        sla = B.settle_due(self.c, self.sla_id)
        self.assertEqual(sla["next_settle"], 1)
        self.assertEqual(sla["epochs"][0]["result"], "clean")
        self.assertEqual(B.pending(self.c, B.PROVIDER), B.FEE_PER_EPOCH)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), 0)
        self.check_solvent()

    def test_settle_is_permissionless(self):
        B.settle_time(self.c, self.sla_id, 0)
        for caller in (B.STRANGER, B.WATCHER, B.CUSTOMER):
            B.set_caller(caller)
        B.settle_due(self.c, self.sla_id, caller=B.WATCHER)

    def test_breach_refunds_fee_and_charges_penalty_to_the_bond(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.CUSTOMER)
        B.settle_time(self.c, self.sla_id, 0)
        sla = B.settle_due(self.c, self.sla_id)
        self.assertEqual(sla["epochs"][0]["result"], "breach")
        self.assertEqual(sla["breaches"], 1)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.FEE_PER_EPOCH + B.PENALTY)
        self.assertEqual(B.pending(self.c, B.PROVIDER), 0)
        self.assertEqual(sla["bond_left"], str(B.BOND - B.PENALTY))
        self.check_solvent()

    def test_third_party_finder_earns_a_tenth_of_the_penalty(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.WATCHER)
        B.settle_time(self.c, self.sla_id, 0)
        B.settle_due(self.c, self.sla_id)
        tip = B.PENALTY * 1000 // 10000
        self.assertEqual(B.pending(self.c, B.WATCHER), tip)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.FEE_PER_EPOCH + B.PENALTY - tip)
        self.check_solvent()

    def test_provider_cannot_farm_the_tip_from_its_own_bond(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.PROVIDER)
        B.settle_time(self.c, self.sla_id, 0)
        B.settle_due(self.c, self.sla_id)
        self.assertEqual(B.pending(self.c, B.PROVIDER), 0)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.FEE_PER_EPOCH + B.PENALTY)

    def test_only_the_first_prover_of_the_epoch_earns_the_tip(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.WATCHER)
        B.prove_outage(self.c, self.sla_id, epoch=0, offset=5 * B.GAP, caller=B.WATCHER2)
        B.settle_time(self.c, self.sla_id, 0)
        B.settle_due(self.c, self.sla_id)
        self.assertEqual(B.pending(self.c, B.WATCHER2), 0)
        self.assertEqual(B.get(self.c, self.sla_id)["breaches"], 1)

    def test_penalty_is_per_epoch_not_per_outage(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.WATCHER)
        B.prove_outage(self.c, self.sla_id, epoch=0, offset=5 * B.GAP, caller=B.WATCHER)
        B.settle_time(self.c, self.sla_id, 0)
        sla = B.settle_due(self.c, self.sla_id)
        self.assertEqual(int(sla["bond_left"]), B.BOND - B.PENALTY)

    def test_penalty_is_capped_by_the_remaining_bond(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, bond_bps=1000, penalty_bps=10000, max_breaches=3)
        bond = int(B.get(c, sla_id)["bond_amount"])
        for epoch in range(3):
            B.prove_outage(c, sla_id, epoch=epoch, caller=B.CUSTOMER)
        B.CLOCK.set_epoch(B.get(c, sla_id)["end_epoch"] + B.SETTLE_DELAY + 1)
        sla = B.settle_due(c, sla_id)
        self.assertEqual(sla["status"], "ended")
        paid_penalty = sum(int(e.get("penalty", 0)) for e in sla["epochs"])
        self.assertEqual(paid_penalty, bond)
        self.assertEqual(B.pending(c, B.PROVIDER), 0)
        self.assertEqual(B.pending(c, B.CUSTOMER), B.TOTAL + bond)
        self.assertTrue(B.accounting(c)["solvent"])

    def test_zero_penalty(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, penalty_bps=0)
        B.prove_outage(c, sla_id, caller=B.WATCHER)
        B.settle_time(c, sla_id, 0)
        B.settle_due(c, sla_id)
        self.assertEqual(B.pending(c, B.CUSTOMER), B.FEE_PER_EPOCH)
        self.assertEqual(B.pending(c, B.WATCHER), 0)

    def test_settles_all_due_epochs_in_one_call_and_ends(self):
        B.prove_outage(self.c, self.sla_id, epoch=1, caller=B.CUSTOMER)
        sla = B.run_to_end(self.c, self.sla_id)
        self.assertEqual([e["result"] for e in sla["epochs"]], ["clean", "breach", "clean"])
        self.assertEqual(sla["status"], "ended")
        self.assertEqual(sla["bond_left"], "0")
        self.assertEqual(B.pending(self.c, B.PROVIDER), 2 * B.FEE_PER_EPOCH + (B.BOND - B.PENALTY))
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.FEE_PER_EPOCH + B.PENALTY)
        self.assertEqual(B.accounting(self.c)["locked"], "0")
        self.check_solvent()

    def test_settle_in_steps(self):
        for epoch in range(B.EPOCHS):
            B.settle_time(self.c, self.sla_id, epoch)
            sla = B.settle_due(self.c, self.sla_id)
            self.assertEqual(sla["next_settle"], epoch + 1)
        self.assertEqual(sla["status"], "ended")

    def test_cannot_settle_twice(self):
        B.run_to_end(self.c, self.sla_id)
        B.set_caller(B.STRANGER)
        B.expect_user_error(self, self.c.settle_due, self.sla_id)

    def test_no_probes_means_the_provider_is_paid(self):
        B.run_to_end(self.c, self.sla_id)
        self.assertEqual(B.pending(self.c, B.PROVIDER), B.TOTAL + B.BOND)

    def test_everything_withdraws_and_the_contract_ends_empty(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.WATCHER)
        B.run_to_end(self.c, self.sla_id)
        total = sum(B.withdraw(self.c, who) for who in (B.PROVIDER, B.CUSTOMER, B.WATCHER))
        self.assertEqual(total, B.TOTAL + B.BOND)
        self.assertEqual(int(self.c.balance), 0)
        acc = B.accounting(self.c)
        self.assertEqual((acc["locked"], acc["owed"], acc["solvent"]), ("0", "0", True))

    def test_double_withdraw_fails(self):
        B.run_to_end(self.c, self.sla_id)
        B.withdraw(self.c, B.PROVIDER)
        B.set_caller(B.PROVIDER)
        B.expect_user_error(self, self.c.withdraw)

    def test_stats_count_ended(self):
        B.run_to_end(self.c, self.sla_id)
        stats = __import__("json").loads(self.c.get_stats())
        self.assertEqual((stats["ended"], stats["live"], stats["total"]), (1, 0, 1))


class TerminateAndResign(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)

    def two_breaches(self):
        for epoch in (0, 1):
            B.prove_outage(self.c, self.sla_id, epoch=epoch, caller=B.CUSTOMER)

    def test_terminate_needs_enough_settled_breaches(self):
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.CUSTOMER)
        B.settle_time(self.c, self.sla_id, 0)
        B.set_caller(B.CUSTOMER)
        B.expect_user_error(self, self.c.terminate_for_cause, self.sla_id)
        self.assertEqual(B.get(self.c, self.sla_id)["next_settle"], 0, "a failed call must not settle anything")

    def test_terminate_settles_due_epochs_first_then_refunds_the_rest(self):
        self.two_breaches()
        B.settle_time(self.c, self.sla_id, 1)
        B.set_caller(B.CUSTOMER)
        self.c.terminate_for_cause(self.sla_id)
        sla = B.get(self.c, self.sla_id)
        self.assertEqual(sla["status"], "terminated")
        self.assertEqual([e["result"] for e in sla["epochs"]], ["breach", "breach", "refunded"])
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.TOTAL + 2 * B.PENALTY)
        self.assertEqual(B.pending(self.c, B.PROVIDER), B.BOND - 2 * B.PENALTY)
        self.assertEqual(B.accounting(self.c)["locked"], "0")
        self.assertTrue(B.accounting(self.c)["solvent"])

    def test_terminate_only_by_the_customer(self):
        self.two_breaches()
        B.settle_time(self.c, self.sla_id, 1)
        for who in (B.PROVIDER, B.STRANGER, B.WATCHER):
            B.set_caller(who)
            B.expect_user_error(self, self.c.terminate_for_cause, self.sla_id)

    def test_good_epochs_are_paid_before_termination(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, max_breaches=1, epochs=4)
        B.prove_outage(c, sla_id, epoch=2, caller=B.CUSTOMER)
        B.settle_time(c, sla_id, 2)
        B.set_caller(B.CUSTOMER)
        c.terminate_for_cause(sla_id)
        fee = B.FEE_PER_EPOCH
        self.assertEqual(B.pending(c, B.PROVIDER), 2 * fee + (int(B.get(c, sla_id)["bond_amount"]) - B.PENALTY))
        self.assertEqual(B.pending(c, B.CUSTOMER), 2 * fee + B.PENALTY)

    def test_cannot_terminate_once_everything_is_settled(self):
        self.two_breaches()
        B.run_to_end(self.c, self.sla_id)
        B.set_caller(B.CUSTOMER)
        B.expect_user_error(self, self.c.terminate_for_cause, self.sla_id)

    def test_resign_refunds_unsettled_epochs_and_forfeits_one_penalty(self):
        B.at(self.c, self.sla_id, 0, 10)
        B.set_caller(B.PROVIDER)
        self.c.resign(self.sla_id)
        sla = B.get(self.c, self.sla_id)
        self.assertEqual(sla["status"], "resigned")
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.TOTAL + B.PENALTY)
        self.assertEqual(B.pending(self.c, B.PROVIDER), B.BOND - B.PENALTY)
        self.assertTrue(B.accounting(self.c)["solvent"])

    def test_resign_pays_for_epochs_already_served(self):
        B.settle_time(self.c, self.sla_id, 0)
        B.set_caller(B.PROVIDER)
        self.c.resign(self.sla_id)
        self.assertEqual(B.pending(self.c, B.PROVIDER), B.FEE_PER_EPOCH + B.BOND - B.PENALTY)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), 2 * B.FEE_PER_EPOCH + B.PENALTY)

    def test_resign_cannot_dodge_penalties_of_unsettled_breaches(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, bond_bps=10000)
        for epoch in (0, 1):
            B.prove_outage(c, sla_id, epoch=epoch, caller=B.WATCHER)
        B.at(c, sla_id, 2, 10)
        B.set_caller(B.PROVIDER)
        c.resign(sla_id)
        sla = B.get(c, sla_id)
        self.assertEqual([e["result"] for e in sla["epochs"]], ["breach", "breach", "refunded"])
        tip = B.PENALTY // 10
        self.assertEqual(B.pending(c, B.WATCHER), 2 * tip)
        self.assertEqual(B.pending(c, B.CUSTOMER), B.TOTAL + 3 * B.PENALTY - 2 * tip)
        self.assertEqual(B.pending(c, B.PROVIDER), B.TOTAL - 3 * B.PENALTY)
        self.assertEqual(B.accounting(c)["locked"], "0")
        self.assertTrue(B.accounting(c)["solvent"])

    def test_terminate_charges_a_proven_outage_in_an_unsettled_epoch(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, max_breaches=1)
        B.prove_outage(c, sla_id, epoch=0, caller=B.CUSTOMER)
        B.prove_outage(c, sla_id, epoch=1, caller=B.WATCHER)
        B.settle_time(c, sla_id, 0)
        B.set_caller(B.CUSTOMER)
        c.terminate_for_cause(sla_id)
        sla = B.get(c, sla_id)
        self.assertEqual([e["result"] for e in sla["epochs"]], ["breach", "breach", "refunded"])
        self.assertEqual(B.pending(c, B.WATCHER), B.PENALTY // 10)
        self.assertTrue(B.accounting(c)["solvent"])

    def test_resign_only_by_the_provider(self):
        for who in (B.CUSTOMER, B.STRANGER):
            B.set_caller(who)
            B.expect_user_error(self, self.c.resign, self.sla_id)

    def test_resign_after_end_fails(self):
        B.run_to_end(self.c, self.sla_id)
        B.set_caller(B.PROVIDER)
        B.expect_user_error(self, self.c.resign, self.sla_id)

    def test_resign_penalty_is_capped_by_the_bond(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, bond_bps=1000, penalty_bps=10000)
        bond = int(B.get(c, sla_id)["bond_amount"])
        B.set_caller(B.PROVIDER)
        c.resign(sla_id)
        self.assertEqual(B.pending(c, B.CUSTOMER), B.TOTAL + min(bond, B.FEE_PER_EPOCH))
        self.assertTrue(B.accounting(c)["solvent"])

    def test_probes_stop_after_termination_or_resignation(self):
        B.at(self.c, self.sla_id, 0, 10)
        B.set_caller(B.PROVIDER)
        self.c.resign(self.sla_id)
        with self.assertRaises(B.gl.vm.UserError):
            B.probe_up(self.c, self.sla_id)


if __name__ == "__main__":
    unittest.main()
