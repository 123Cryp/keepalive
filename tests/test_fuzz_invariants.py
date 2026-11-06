"""Randomised end-to-end simulation. After every operation the solvency
identity must hold (balance == locked + owed), and when every agreement has
been wound down and every balance withdrawn the contract must be empty and
every wei deposited must have been paid out exactly once."""
import json
import random
import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B

WALLETS = ["0x" + f"{0xA0 + i:02x}" * 20 for i in range(6)]
FEED = "https://www.githubstatus.com/api/v2/incidents.json"


class Simulation:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.c = B.make_contract()
        self.deposited = 0
        self.withdrawn = 0
        self.ids = []

    def solvent(self):
        acc = B.accounting(self.c)
        assert acc["solvent"], acc
        assert int(self.c.balance) == self.deposited - self.withdrawn, (int(self.c.balance), self.deposited, self.withdrawn)

    def payable(self, sender, method, value, *args):
        B.set_caller(sender)
        try:
            result = B.call_payable(self.c, method, value, *args)
        except B.PaymentRejected:
            result = None
        except B.gl.vm.UserError:
            return None
        self.deposited += value
        return result

    def op_create(self):
        r = self.rng
        customer, provider = r.sample(WALLETS, 2)
        epochs = r.randint(1, 5)
        fee = r.randint(1, 50) * 10
        args = B.default_args(
            provider_wallet=provider, epochs=epochs, bond_bps=r.choice([1000, 5000, 20000]),
            penalty_bps=r.choice([0, 2500, 10000]), max_breaches=r.randint(1, epochs),
            status_feed=r.choice(["", FEED]), outage_probes=r.randint(2, 4), min_gap_seconds=60,
            window_seconds=r.choice([600, 900]),
        )
        value = fee * epochs + r.choice([0, 0, 0, 1])
        result = self.payable(customer, "create_sla", value, *[args[k] for k in B.ARG_ORDER])
        if result and result.startswith("sla_"):
            self.ids.append(result)

    def op_accept(self):
        if not self.ids:
            return
        sla_id = self.rng.choice(self.ids)
        sla = B.get(self.c, sla_id)
        bond = int(sla["bond_amount"]) + self.rng.choice([0, 0, 0, 1])
        self.payable(self.rng.choice([sla["provider"], self.rng.choice(WALLETS)]), "accept_sla", bond, sla_id)

    def op_cancel(self):
        if self.ids:
            self.try_call(self.c.cancel_sla, self.rng.choice(self.ids), who=None)

    def op_probe(self):
        if not self.ids:
            return
        sla_id = self.rng.choice(self.ids)
        sla = B.get(self.c, sla_id)
        if sla["status"] == "active" and self.rng.random() < 0.7:
            B.CLOCK.set_epoch(sla["start_epoch"] + self.rng.randint(0, sla["end_epoch"] - sla["start_epoch"] - 1))
        body = self.rng.choice([B.healthy_body(sla_id, sla["provider"]), B.down_body(sla_id, sla["provider"]),
                                B.down_body(sla_id, sla["provider"]), RuntimeError("503")])
        try:
            B.probe(self.c, sla_id, body, caller=self.rng.choice(WALLETS))
        except B.gl.vm.UserError:
            pass

    def op_exemption(self):
        if not self.ids:
            return
        sla_id = self.rng.choice(self.ids)
        sla = B.get(self.c, sla_id)
        windows = [w for e in sla["epochs"] for w in e["windows"]]
        start = windows[0][0] - 30 if windows else 0
        body = {"incidents": [{"id": "i", "impact": "major", "started_at": B.gh(start) if hasattr(B, "gh") else "2026-01-01T00:00:00Z",
                               "resolved_at": None, "components": []}]}
        from unittest.mock import patch
        web = B.Web(body)
        B.set_caller(sla["provider"])
        with patch.object(B.gl.nondet.web, "render", side_effect=web.render):
            try:
                self.c.file_exemption(sla_id, self.rng.randint(0, sla["epochs_total"] - 1))
            except B.gl.vm.UserError:
                pass

    def op_time(self):
        B.CLOCK.advance(self.rng.choice([60, 3600, 86400, 3 * 86400]))

    def op_settle(self):
        if self.ids:
            self.try_call(self.c.settle_due, self.rng.choice(self.ids), who=self.rng.choice(WALLETS))

    def op_terminate(self):
        if self.ids:
            sla_id = self.rng.choice(self.ids)
            self.try_call(self.c.terminate_for_cause, sla_id, who=B.get(self.c, sla_id)["customer"])

    def op_resign(self):
        if self.ids:
            sla_id = self.rng.choice(self.ids)
            self.try_call(self.c.resign, sla_id, who=B.get(self.c, sla_id)["provider"])

    def op_withdraw(self):
        who = self.rng.choice(WALLETS)
        B.set_caller(who)
        try:
            self.withdrawn += B.withdraw(self.c, who)
        except B.gl.vm.UserError:
            pass

    def try_call(self, fn, sla_id, who):
        if who is None:
            sla = B.get(self.c, sla_id)
            who = sla["customer"]
        B.set_caller(who)
        try:
            fn(sla_id)
        except B.gl.vm.UserError:
            pass

    def step(self):
        ops = [self.op_create, self.op_create, self.op_accept, self.op_accept, self.op_cancel, self.op_probe,
               self.op_probe, self.op_probe, self.op_probe, self.op_exemption, self.op_time, self.op_time,
               self.op_settle, self.op_terminate, self.op_resign, self.op_withdraw]
        self.rng.choice(ops)()
        self.solvent()

    def wind_down(self):
        for sla_id in self.ids:
            sla = B.get(self.c, sla_id)
            if sla["status"] == "open":
                B.set_caller(sla["customer"])
                self.c.cancel_sla(sla_id)
            elif sla["status"] == "active":
                B.CLOCK.set_epoch(sla["end_epoch"] + B.SETTLE_DELAY + 1)
                B.set_caller(B.STRANGER)
                self.c.settle_due(sla_id)
            self.solvent()
        for who in WALLETS:
            try:
                self.withdrawn += B.withdraw(self.c, who)
            except B.gl.vm.UserError:
                pass
        self.solvent()


class FuzzTests(unittest.TestCase):
    def run_seed(self, seed, steps=400):
        sim = Simulation(seed)
        for _ in range(steps):
            sim.step()
        sim.wind_down()
        acc = B.accounting(sim.c)
        self.assertEqual((acc["balance"], acc["locked"], acc["owed"]), ("0", "0", "0"), seed)
        self.assertEqual(sim.deposited, sim.withdrawn, seed)
        for sla_id in sim.ids:
            self.assertIn(B.get(sim.c, sla_id)["status"], ("ended", "cancelled", "terminated", "resigned"))
        return sim

    def test_seeds(self):
        total_slas = 0
        for seed in range(40):
            with self.subTest(seed=seed):
                total_slas += len(self.run_seed(seed).ids)
        self.assertGreater(total_slas, 100)

    def test_long_run(self):
        self.run_seed(9001, steps=3000)

    def test_every_status_is_reachable_in_simulation(self):
        seen = set()
        for seed in range(40):
            sim = Simulation(seed)
            for _ in range(400):
                sim.step()
            for sla_id in sim.ids:
                seen.add(B.get(sim.c, sla_id)["status"])
        self.assertTrue({"open", "active", "cancelled", "resigned"} <= seen, seen)


if __name__ == "__main__":
    unittest.main()
