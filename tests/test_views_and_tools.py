import ast
import json
import os
import subprocess
import sys
import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Views(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()

    def test_empty_state(self):
        self.assertEqual(json.loads(self.c.list_slas(0, 10))["slas"], [])
        self.assertEqual(json.loads(self.c.get_stats()), {"cancelled": 0, "ended": 0, "live": 0, "total": 0})
        self.assertEqual(self.c.total_slas(), 0)
        self.assertEqual(self.c.get_contract_balance(), "0")
        self.assertEqual(self.c.get_pending_withdrawal(B.CUSTOMER), "0")
        self.assertEqual(B.accounting(self.c), {"balance": "0", "locked": "0", "owed": "0", "solvent": True})

    def test_list_is_newest_first_and_paged(self):
        for _ in range(5):
            B.create_sla(self.c)
        page = json.loads(self.c.list_slas(0, 2))
        self.assertEqual([r["sla_id"] for r in page["slas"]], ["sla_4", "sla_3"])
        self.assertEqual(page["total"], 5)
        page = json.loads(self.c.list_slas(2, 2))
        self.assertEqual([r["sla_id"] for r in page["slas"]], ["sla_2", "sla_1"])
        page = json.loads(self.c.list_slas(4, 50))
        self.assertEqual([r["sla_id"] for r in page["slas"]], ["sla_0"])
        self.assertEqual(json.loads(self.c.list_slas(99, 5))["slas"], [])

    def test_list_limit_is_clamped(self):
        for _ in range(3):
            B.create_sla(self.c)
        self.assertEqual(json.loads(self.c.list_slas(0, 0))["limit"], 1)
        self.assertEqual(json.loads(self.c.list_slas(0, 10 ** 6))["limit"], 50)
        self.assertEqual(len(json.loads(self.c.list_slas(-5, 2))["slas"]), 2)

    def test_summary_carries_what_the_explorer_needs(self):
        sla_id = B.active_sla(self.c)
        B.prove_outage(self.c, sla_id)
        row = json.loads(self.c.list_slas(0, 1))["slas"][0]
        for key in ("sla_id", "status", "service_name", "customer", "provider", "health_url", "total_fee",
                    "fee_per_epoch", "bond_amount", "epochs_total", "epoch_seconds", "start_epoch", "end_epoch",
                    "next_settle", "breaches", "excused", "max_breaches", "up_count", "down_count",
                    "counted_downs", "outage_probes", "min_gap_seconds", "end_reason"):
            self.assertIn(key, row)
        self.assertNotIn("log", row)

    def test_get_sla_and_unknown_id(self):
        sla_id = B.create_sla(self.c)
        self.assertEqual(json.loads(self.c.get_sla(sla_id))["sla_id"], sla_id)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.get_sla("sla_9")
        with self.assertRaises(B.gl.vm.UserError):
            self.c.get_binding_tag("sla_9")

    def test_binding_tag(self):
        sla_id = B.create_sla(self.c)
        self.assertEqual(self.c.get_binding_tag(sla_id), f"keepalive:{sla_id}:{B.PROVIDER}")

    def test_stats_track_the_lifecycle(self):
        a = B.active_sla(self.c)
        b = B.create_sla(self.c)
        B.set_caller(B.CUSTOMER)
        self.c.cancel_sla(b)
        self.assertEqual(json.loads(self.c.get_stats()), {"cancelled": 1, "ended": 0, "live": 1, "total": 2})
        B.run_to_end(self.c, a)
        self.assertEqual(json.loads(self.c.get_stats()), {"cancelled": 1, "ended": 1, "live": 0, "total": 2})

    def test_a_failed_terminate_leaves_no_trace(self):
        sla_id = B.active_sla(self.c)
        B.prove_outage(self.c, sla_id, epoch=0, caller=B.CUSTOMER)
        B.settle_time(self.c, sla_id, 0)
        before = (dict(self.c.slas), dict(self.c.pending_withdrawals), int(self.c.locked_total), int(self.c.owed_total))
        B.set_caller(B.CUSTOMER)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.terminate_for_cause(sla_id)
        after = (dict(self.c.slas), dict(self.c.pending_withdrawals), int(self.c.locked_total), int(self.c.owed_total))
        self.assertEqual(before, after)
        self.assertTrue(B.accounting(self.c)["solvent"])


class Tools(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)

    def test_deploy_file_is_up_to_date(self):
        result = self.run_tool("tools/make_deploy.py", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_testnet_file_is_up_to_date(self):
        result = self.run_tool("tools/make_testnet_deploy.py", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_deploy_file_is_the_same_program(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import make_deploy
        finally:
            sys.path.pop(0)
        with open(os.path.join(ROOT, "contract.py"), encoding="utf-8") as handle:
            source = handle.read()
        with open(os.path.join(ROOT, "contract_deploy.py"), encoding="utf-8") as handle:
            deploy = handle.read()
        expected = make_deploy.remove_docstrings_from_tree(ast.parse("\n".join(source.splitlines()[2:]) + "\n"))
        actual = ast.parse("\n".join(deploy.splitlines()[2:]) + "\n")
        self.assertEqual(ast.dump(expected), ast.dump(actual))
        self.assertEqual(deploy.splitlines()[:2], source.splitlines()[:2])

    def test_testnet_build_changes_only_time_constants(self):
        with open(os.path.join(ROOT, "contract_deploy.py"), encoding="utf-8") as handle:
            prod = handle.read().splitlines()
        with open(os.path.join(ROOT, "contract_testnet_deploy.py"), encoding="utf-8") as handle:
            test = handle.read().splitlines()
        self.assertEqual(len(prod), len(test))
        changed = [(a, b) for a, b in zip(prod, test) if a != b]
        self.assertEqual(len(changed), 4)
        for old, new in changed:
            self.assertRegex(old, r"^[A-Z_]+_SECONDS = ")
            self.assertEqual(old.split(" = ")[0], new.split(" = ")[0])

    def test_testnet_windows_are_short_but_still_valid(self):
        with open(os.path.join(ROOT, "contract_testnet_deploy.py"), encoding="utf-8") as handle:
            text = handle.read()
        for line in ("MIN_EPOCH_SECONDS = 300", "WARMUP_SECONDS = 60", "MIN_GAP_SECONDS = 30",
                     "SETTLE_DELAY_SECONDS = 300"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
