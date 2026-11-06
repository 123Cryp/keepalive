import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B


class ProbeGating(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)

    def test_not_before_start(self):
        B.CLOCK.set_epoch(B.start_of(self.c, self.sla_id) - 1)
        B.set_caller(B.WATCHER)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            B.probe_up(self.c, self.sla_id)
        self.assertIn("not started", str(ctx.exception))

    def test_not_after_end(self):
        B.CLOCK.set_epoch(B.get(self.c, self.sla_id)["end_epoch"])
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            B.probe_up(self.c, self.sla_id)
        self.assertIn("ended", str(ctx.exception))

    def test_only_active_agreements(self):
        open_id = B.create_sla(self.c)
        B.at(self.c, self.sla_id, 0, 5)
        with self.assertRaises(B.gl.vm.UserError):
            B.probe_up(self.c, open_id)
        with self.assertRaises(B.gl.vm.UserError):
            B.probe_up(self.c, "sla_77")

    def test_first_second_and_last_second_are_inside(self):
        B.at(self.c, self.sla_id, 0, 0)
        self.assertEqual(B.probe_up(self.c, self.sla_id)["epoch"], 0)
        sla = B.get(self.c, self.sla_id)
        B.CLOCK.set_epoch(sla["end_epoch"] - 1)
        self.assertEqual(B.probe_up(self.c, self.sla_id, B.WATCHER2)["epoch"], B.EPOCHS - 1)

    def test_epoch_index_follows_time(self):
        for epoch in range(B.EPOCHS):
            B.at(self.c, self.sla_id, epoch, 10)
            self.assertEqual(B.probe_up(self.c, self.sla_id, caller="0x" + f"{epoch + 60:02x}" * 20)["epoch"], epoch)

    def test_wallet_cooldown(self):
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_up(self.c, self.sla_id)
        B.at(self.c, self.sla_id, 0, B.GAP - 1)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            B.probe_up(self.c, self.sla_id)
        self.assertIn("too recently", str(ctx.exception))
        B.at(self.c, self.sla_id, 0, B.GAP)
        B.probe_up(self.c, self.sla_id)

    def test_cooldown_is_per_wallet_and_per_agreement(self):
        other = B.active_sla(self.c)
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_up(self.c, self.sla_id, B.WATCHER)
        B.probe_up(self.c, self.sla_id, B.WATCHER2)
        B.CLOCK.set_epoch(B.get(self.c, other)["start_epoch"] + 1)
        B.probe_up(self.c, other, B.WATCHER, body=B.healthy_body(other))

    def test_failed_probe_does_not_consume_cooldown(self):
        B.at(self.c, self.sla_id, 0, 0)
        with self.assertRaises(B.gl.vm.UserError):
            B.probe(self.c, self.sla_id, RuntimeError("429"))
        B.probe_up(self.c, self.sla_id)

    def test_unreadable_changes_nothing(self):
        B.at(self.c, self.sla_id, 0, 0)
        before = dict(self.c.slas), dict(self.c.prober_last)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            B.probe(self.c, self.sla_id, RuntimeError("Too Many Requests"))
        self.assertIn("could not be judged", str(ctx.exception))
        self.assertEqual((dict(self.c.slas), dict(self.c.prober_last)), before)

    def test_stats_and_log(self):
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_up(self.c, self.sla_id)
        B.at(self.c, self.sla_id, 0, 400)
        B.probe_down(self.c, self.sla_id, B.WATCHER2)
        sla = B.get(self.c, self.sla_id)
        self.assertEqual((sla["up_count"], sla["down_count"], sla["probe_count"]), (1, 1, 2))
        self.assertEqual([row[1] for row in sla["log"]], ["UP", "DOWN"])
        self.assertEqual(sla["epochs"][0]["ups"], 1)
        self.assertEqual(sla["epochs"][0]["downs"], 1)

    def test_log_is_bounded(self):
        for i in range(B.m.MAX_LOG_ENTRIES + 10):
            B.at(self.c, self.sla_id, 0, i * B.GAP)
            B.probe_up(self.c, self.sla_id)
        self.assertEqual(len(B.get(self.c, self.sla_id)["log"]), B.m.MAX_LOG_ENTRIES)

    def test_up_budget_per_epoch_never_blocks_down_probes(self):
        B.m.MAX_UP_PROBES_PER_EPOCH = 3
        try:
            for i in range(3):
                B.at(self.c, self.sla_id, 0, i * B.GAP)
                B.probe_up(self.c, self.sla_id)
            B.at(self.c, self.sla_id, 0, 3 * B.GAP)
            with self.assertRaises(B.gl.vm.UserError) as ctx:
                B.probe_up(self.c, self.sla_id)
            self.assertIn("budget", str(ctx.exception))
            result = B.probe_down(self.c, self.sla_id)
            self.assertEqual(result["result"], "DOWN")
            B.at(self.c, self.sla_id, 1, 0)
            B.probe_up(self.c, self.sla_id)
        finally:
            B.m.MAX_UP_PROBES_PER_EPOCH = 200


class OutageRule(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)

    def epoch0(self):
        return B.get(self.c, self.sla_id)["epochs"][0]

    def test_k_spaced_downs_prove_an_outage(self):
        result = B.prove_outage(self.c, self.sla_id)
        self.assertTrue(result["outage_proven"])
        rec = self.epoch0()
        self.assertEqual(rec["outages"], 1)
        self.assertEqual(rec["finder"], B.WATCHER)
        self.assertEqual(rec["windows"], [[B.start_of(self.c, self.sla_id), B.start_of(self.c, self.sla_id) + 2 * B.GAP]])
        self.assertEqual(B.get(self.c, self.sla_id)["down_times"], [])

    def test_progress_is_reported(self):
        for i in range(B.K - 1):
            B.at(self.c, self.sla_id, 0, i * B.GAP)
            result = B.probe_down(self.c, self.sla_id)
            self.assertFalse(result["outage_proven"])
            self.assertEqual(result["counted_downs"], i + 1)
            self.assertEqual(result["needed"], B.K)

    def test_fewer_than_k_is_not_an_outage(self):
        B.prove_outage(self.c, self.sla_id, count=B.K - 1)
        self.assertEqual(self.epoch0()["outages"], 0)

    def test_downs_closer_than_the_gap_do_not_count(self):
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_down(self.c, self.sla_id, B.WATCHER)
        B.at(self.c, self.sla_id, 0, B.GAP - 1)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            B.probe_down(self.c, self.sla_id, B.WATCHER2)
        self.assertIn("minimum gap", str(ctx.exception))
        self.assertEqual(B.get(self.c, self.sla_id)["down_count"], 1)

    def test_many_wallets_cannot_compress_the_outage(self):
        wallets = ["0x" + f"{70 + i:02x}" * 20 for i in range(6)]
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_down(self.c, self.sla_id, wallets[0])
        for i, wallet in enumerate(wallets[1:], start=1):
            B.at(self.c, self.sla_id, 0, i)
            with self.assertRaises(B.gl.vm.UserError):
                B.probe_down(self.c, self.sla_id, wallet)
        self.assertEqual(self.epoch0()["outages"], 0)

    def test_downs_from_different_wallets_count_together(self):
        wallets = [B.WATCHER, B.WATCHER2, B.CUSTOMER]
        for i, wallet in enumerate(wallets):
            B.at(self.c, self.sla_id, 0, i * B.GAP)
            result = B.probe_down(self.c, self.sla_id, wallet)
        self.assertTrue(result["outage_proven"])
        self.assertEqual(self.epoch0()["finder"], B.CUSTOMER)

    def test_up_probes_never_erase_failed_probes(self):
        """The anti-spoofing rule: a successful probe, even one fired by the
        provider between two failures, does not reset the evidence."""
        times = [0, 1 * B.GAP, 2 * B.GAP]
        B.at(self.c, self.sla_id, 0, times[0])
        B.probe_down(self.c, self.sla_id, B.WATCHER)
        B.at(self.c, self.sla_id, 0, times[0] + 100)
        B.probe_up(self.c, self.sla_id, B.PROVIDER)
        B.at(self.c, self.sla_id, 0, times[1])
        B.probe_down(self.c, self.sla_id, B.WATCHER)
        B.at(self.c, self.sla_id, 0, times[1] + 100)
        B.probe_up(self.c, self.sla_id, B.PROVIDER)
        B.at(self.c, self.sla_id, 0, times[2])
        result = B.probe_down(self.c, self.sla_id, B.WATCHER)
        self.assertTrue(result["outage_proven"])

    def test_provider_probing_all_day_cannot_block_watchers(self):
        for i in range(10):
            B.at(self.c, self.sla_id, 0, i * 60)
            B.probe_up(self.c, self.sla_id, "0x" + f"{80 + i:02x}" * 20)
        B.prove_outage(self.c, self.sla_id, offset=700)
        self.assertEqual(self.epoch0()["outages"], 1)

    def test_old_downs_fall_out_of_the_window(self):
        B.at(self.c, self.sla_id, 0, 0)
        B.probe_down(self.c, self.sla_id)
        B.at(self.c, self.sla_id, 0, B.WINDOW + 1)
        B.probe_down(self.c, self.sla_id)
        B.at(self.c, self.sla_id, 0, B.WINDOW + 1 + B.GAP)
        result = B.probe_down(self.c, self.sla_id)
        self.assertFalse(result["outage_proven"])
        self.assertEqual(result["counted_downs"], 2)
        self.assertEqual(self.epoch0()["outages"], 0)

    def test_window_edge_is_inclusive(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, window_seconds=2 * B.GAP)
        result = B.prove_outage(c, sla_id)
        self.assertTrue(result["outage_proven"])

    def test_isolated_failures_across_an_epoch_do_not_add_up(self):
        for i in range(10):
            B.at(self.c, self.sla_id, 0, i * 3 * B.WINDOW)
            B.probe_down(self.c, self.sla_id)
        self.assertEqual(self.epoch0()["outages"], 0)

    def test_a_second_outage_needs_a_fresh_set_of_probes(self):
        B.prove_outage(self.c, self.sla_id, offset=0)
        B.at(self.c, self.sla_id, 0, 3 * B.GAP)
        result = B.probe_down(self.c, self.sla_id)
        self.assertFalse(result["outage_proven"])
        self.assertEqual(result["counted_downs"], 1)
        B.prove_outage(self.c, self.sla_id, offset=4 * B.GAP, caller=B.WATCHER2)
        rec = self.epoch0()
        self.assertEqual(rec["outages"], 2)
        self.assertEqual(rec["finder"], B.WATCHER)
        self.assertEqual(len(rec["windows"]), 2)

    def test_windows_list_is_bounded(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, outage_probes=2, window_seconds=600, min_gap_seconds=60)
        for n in range(B.m.MAX_WINDOWS_PER_EPOCH + 3):
            for i in range(2):
                B.at(c, sla_id, 0, (n * 2 + i) * 60)
                B.probe_down(c, sla_id, B.WATCHER if i == 0 else B.WATCHER2)
        rec = B.get(c, sla_id)["epochs"][0]
        self.assertEqual(rec["outages"], B.m.MAX_WINDOWS_PER_EPOCH + 3)
        self.assertEqual(len(rec["windows"]), B.m.MAX_WINDOWS_PER_EPOCH)

    def test_outage_is_charged_to_the_epoch_of_the_proving_probe(self):
        sla = B.get(self.c, self.sla_id)
        boundary = sla["epoch_seconds"]
        for i, offset in enumerate((boundary - 2 * B.GAP, boundary - B.GAP, boundary)):
            B.CLOCK.set_epoch(sla["start_epoch"] + offset)
            result = B.probe_down(self.c, self.sla_id)
        self.assertTrue(result["outage_proven"])
        epochs = B.get(self.c, self.sla_id)["epochs"]
        self.assertEqual((epochs[0]["outages"], epochs[1]["outages"]), (0, 1))

    def test_two_probe_minimum_rule(self):
        c = B.make_contract()
        sla_id = B.active_sla(c, outage_probes=2, window_seconds=600)
        B.at(c, sla_id, 0, 0)
        self.assertFalse(B.probe_down(c, sla_id)["outage_proven"])
        B.at(c, sla_id, 0, B.GAP)
        self.assertTrue(B.probe_down(c, sla_id, B.WATCHER2)["outage_proven"])

    def test_healthy_service_never_accumulates_evidence(self):
        for i in range(30):
            B.at(self.c, self.sla_id, i % 3, (i // 3) * B.GAP)
            B.probe_up(self.c, self.sla_id, "0x" + f"{100 + i:02x}" * 20)
        for epoch in B.get(self.c, self.sla_id)["epochs"]:
            self.assertEqual(epoch["outages"], 0)


if __name__ == "__main__":
    unittest.main()
