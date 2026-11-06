import ast
import os
import unittest

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B

with open(B.CONTRACT_PATH, encoding="utf-8") as _handle:
    SOURCE = _handle.read()
TREE = ast.parse(SOURCE)


def class_node():
    return next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Keepalive")


def decorators(fn):
    names = []
    for d in fn.decorator_list:
        names.append(ast.unparse(d))
    return names


def public_writes():
    out = {}
    for fn in class_node().body:
        if isinstance(fn, ast.FunctionDef):
            decs = decorators(fn)
            if "gl.public.write" in decs or "gl.public.write.payable" in decs:
                out[fn.name] = (fn, "gl.public.write.payable" in decs)
    return out


def references_sender(fn_node, methods):
    """True if the method, or a private helper it calls on self, reads the sender."""
    seen = set()

    def visit(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "sender_address":
                return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self":
                name = sub.func.attr
                if name in methods and name not in seen and name.startswith("_") and name != "_bounce":
                    seen.add(name)
                    if visit(methods[name]):
                        return True
        return False

    return visit(fn_node)


class StaticAudit(unittest.TestCase):
    def setUp(self):
        self.methods = {fn.name: fn for fn in class_node().body if isinstance(fn, ast.FunctionDef)}
        self.writes = public_writes()

    def test_every_public_write_is_classified(self):
        expected = {
            "create_sla", "accept_sla", "cancel_sla", "probe", "file_exemption", "settle_due",
            "terminate_for_cause", "resign", "withdraw",
        }
        self.assertEqual(set(self.writes), expected)

    def test_restricted_writes_check_the_sender(self):
        restricted = {"accept_sla", "cancel_sla", "file_exemption", "terminate_for_cause", "resign"}
        for name in restricted:
            with self.subTest(name=name):
                self.assertTrue(references_sender(self.writes[name][0], self.methods), name)

    def test_permissionless_writes_are_exactly_the_documented_ones(self):
        open_methods = {n for n in self.writes if not references_sender(self.writes[n][0], self.methods)}
        self.assertEqual(open_methods, {"settle_due"})

    def test_probe_uses_sender_only_for_the_cooldown(self):
        src = ast.unparse(self.writes["probe"][0])
        self.assertEqual(src.count("sender_address"), 1)
        self.assertIn("cooldown_key", src)

    def test_payable_methods_bounce_instead_of_reverting(self):
        payables = {n for n, (_fn, payable) in self.writes.items() if payable}
        self.assertEqual(payables, {"create_sla", "accept_sla"})
        for name in payables:
            fn = self.writes[name][0]
            tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
            self.assertEqual(len(tries), 1, name)
            handler = tries[0].handlers[0]
            self.assertIn("gl.vm.UserError", ast.unparse(handler.type))
            self.assertIn("_bounce", ast.unparse(handler))

    def test_non_payable_writes_never_credit_attached_gen(self):
        for name, (fn, payable) in self.writes.items():
            if not payable:
                self.assertNotIn("message.value", ast.unparse(fn), name)

    def test_only_withdraw_moves_gen_out(self):
        for fn in ast.walk(TREE):
            if isinstance(fn, ast.FunctionDef) and "emit_transfer" in ast.unparse(fn):
                self.assertEqual(fn.name, "withdraw")

    def test_withdraw_zeroes_the_ledger_before_the_transfer(self):
        src = ast.unparse(self.writes["withdraw"][0])
        self.assertLess(src.index("= u256(0)"), src.index("emit_transfer"))

    def test_money_only_leaves_the_locked_pool_through_release(self):
        for fn in class_node().body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            text = ast.unparse(fn)
            if fn.name not in ("_release", "_credit", "_lock", "withdraw", "_bounce", "__init__") \
                    and "gl.public.view" not in decorators(fn):
                self.assertNotIn("pending_withdrawals[", text, fn.name)
                self.assertNotIn("self.locked_total =", text, fn.name)
                self.assertNotIn("self.owed_total =", text, fn.name)

    def test_one_clock(self):
        for fn in ast.walk(TREE):
            if isinstance(fn, ast.FunctionDef) and fn.name != "_now_utc":
                self.assertNotIn("datetime.now(", ast.unparse(fn), fn.name)
                self.assertNotIn(".utcnow(", ast.unparse(fn), fn.name)

    def test_no_nondeterministic_closure_touches_self(self):
        for fn in TREE.body:
            if isinstance(fn, ast.FunctionDef):
                args = [a.arg for a in fn.args.args]
                self.assertNotIn("self", args, fn.name)
        for name in ("_run_probe", "_run_feed"):
            fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertNotIn("self", ast.unparse(fn))
            self.assertIn("strict_eq", ast.unparse(fn))

    def test_no_run_nondet_keyword_arguments_or_unsafe_storage_patterns(self):
        self.assertNotIn("inmem_allocate", SOURCE)
        self.assertNotIn("datetime.min", SOURCE)
        self.assertNotIn("run_nondet_unsafe(leader_fn=", SOURCE)

    def test_storage_is_declared_by_class_annotations_and_never_assigned_empty(self):
        init = next(f for f in class_node().body if isinstance(f, ast.FunctionDef) and f.name == "__init__")
        text = ast.unparse(init)
        self.assertNotIn("{}", text)
        self.assertNotIn("[]", text)
        self.assertNotIn("TreeMap", text)

    def test_deploy_header(self):
        lines = SOURCE.splitlines()
        self.assertEqual(lines[0], "# v0.2.16")
        self.assertTrue(lines[1].startswith('# { "Depends": "py-genlayer:'))

    def test_deploy_file_has_no_comments_or_docstrings(self):
        path = os.path.join(os.path.dirname(B.CONTRACT_PATH), "contract_deploy.py")
        with open(path, encoding="utf-8") as handle:
            deploy = handle.read()
        tree = ast.parse(deploy)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                self.assertIsNone(ast.get_docstring(node))
        body = deploy.splitlines()[2:]
        self.assertFalse([line for line in body if line.strip().startswith("#")])


class BehaviouralAuthorization(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c)
        B.prove_outage(self.c, self.sla_id, caller=B.WATCHER)
        B.CLOCK.set_epoch(B.get(self.c, self.sla_id)["start_epoch"] + B.EPOCH_SECONDS + 10)

    def test_strangers_cannot_use_restricted_methods(self):
        B.set_caller(B.STRANGER)
        for call in (lambda: self.c.cancel_sla(self.sla_id), lambda: self.c.terminate_for_cause(self.sla_id),
                     lambda: self.c.resign(self.sla_id), lambda: self.c.file_exemption(self.sla_id, 0)):
            with self.assertRaises(B.gl.vm.UserError):
                call()

    def test_counterparties_cannot_use_each_others_methods(self):
        B.set_caller(B.CUSTOMER)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.resign(self.sla_id)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.file_exemption(self.sla_id, 0)
        B.set_caller(B.PROVIDER)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.terminate_for_cause(self.sla_id)
        with self.assertRaises(B.gl.vm.UserError):
            self.c.cancel_sla(self.sla_id)

    def test_withdraw_only_moves_the_callers_own_balance(self):
        B.run_to_end(self.c, self.sla_id)
        owed_before = B.pending(self.c, B.CUSTOMER)
        B.set_caller(B.STRANGER)
        B.expect_user_error(self, self.c.withdraw)
        self.assertEqual(B.pending(self.c, B.CUSTOMER), owed_before)

    def test_attached_gen_on_accept_cannot_be_kept_by_a_wrong_sender(self):
        c = B.make_contract()
        sla_id = B.create_sla(c)
        before = B.pending(c, B.STRANGER)
        B.set_caller(B.STRANGER)
        with self.assertRaises(B.PaymentRejected):
            B.call_payable(c, "accept_sla", B.BOND, sla_id)
        self.assertEqual(B.pending(c, B.STRANGER) - before, B.BOND)
        self.assertTrue(B.accounting(c)["solvent"])


if __name__ == "__main__":
    unittest.main()
