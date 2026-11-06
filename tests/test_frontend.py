import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as _h:
    HTML = _h.read()
with open(os.path.join(ROOT, "contract.py"), encoding="utf-8") as _h:
    TREE = ast.parse(_h.read())


def public_methods():
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Keepalive")
    out = {}
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            decs = [ast.unparse(d) for d in fn.decorator_list]
            if any(d.startswith("gl.public") for d in decs):
                out[fn.name] = [a.arg for a in fn.args.args if a.arg != "self"]
    return out


class Frontend(unittest.TestCase):
    def test_every_contract_call_exists_with_the_right_arity(self):
        methods = public_methods()
        calls = re.findall(r'(?:read|readJson)\("([a-z_]+)"(?:, \[([^\]]*)\])?', HTML)
        calls += [(n, a) for n, a in re.findall(r'write\([^,]+, [^,]+, "([a-z_]+)", \[([^\]]*)\]', HTML)]
        self.assertGreater(len(calls), 8)
        for name, args in calls:
            self.assertIn(name, methods, name)
            if name in ("create_sla", "file_exemption"):
                continue
            given = len([a for a in args.split(",") if a.strip()]) if args else 0
            if name in ("list_slas",):
                given = 2
            self.assertEqual(given, len(methods[name]), name)

    def test_exemption_call_passes_id_and_epoch(self):
        self.assertIn('"file_exemption", [id, parseInt(sel.value, 10)]', HTML)
        self.assertEqual(public_methods()["file_exemption"], ["sla_id", "epoch_index"])

    def test_create_call_passes_every_argument_in_order(self):
        order = public_methods()["create_sla"]
        m = re.search(r'"create_sla",\s*\[([^\]]+)\]', HTML)
        self.assertEqual(len([a for a in m.group(1).split(",")]), len(order))
        self.assertEqual(order[0], "provider_wallet")

    def test_every_write_method_is_reachable_from_the_ui(self):
        for name in ("create_sla", "accept_sla", "cancel_sla", "probe", "file_exemption", "settle_due",
                     "terminate_for_cause", "resign", "withdraw"):
            self.assertIn(f'"{name}"', HTML, name)

    def test_ids_used_by_script_exist(self):
        ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', HTML))
        used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', HTML))
        self.assertFalse(used - ids, used - ids)

    def test_contract_data_is_only_ever_inserted_as_text(self):
        self.assertNotIn("innerHTML", HTML)
        self.assertNotIn("insertAdjacentHTML", HTML)
        self.assertNotIn("document.write", HTML)

    def test_routes_exist(self):
        for route in ("#/", "#/create", "#/watch", "#/balance", "#/sla/"):
            self.assertIn(route, HTML)
        for section in ("route-home", "route-create", "route-sla", "route-watch", "route-balance"):
            self.assertIn(f'id="{section}"', HTML)

    def test_wallet_wiring_follows_the_known_good_pattern(self):
        self.assertIn("genlayer-js@latest", HTML)
        self.assertNotIn("client.connect(", HTML)
        self.assertNotIn("initializeConsensusSmartContract", HTML)
        self.assertEqual(len(re.findall(r"createClient\(\{ chain: studionet, account", HTML)), 3)

    def test_no_browser_dialogs_and_accessible_basics(self):
        self.assertNotIn("alert(", HTML)
        self.assertIn("prefers-reduced-motion", HTML)
        self.assertIn("prefers-color-scheme: dark", HTML)
        self.assertIn(":focus-visible", HTML)
        self.assertIn('name="viewport"', HTML)


if __name__ == "__main__":
    unittest.main()
