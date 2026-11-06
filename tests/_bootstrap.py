"""
Shared test bootstrap: wires up the offline genlayer SDK stub, loads the
contract once (contract.py by default, or the file named by the KA_CONTRACT
environment variable, which is how the stripped deploy file is tested),
replaces the contract's single clock with a controllable one, and provides
builders for agreements, health responses and flows.
"""
import datetime as _dt
import importlib.util
import json as _json
import os
import sys
from unittest.mock import patch as _patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

CONTRACT_PATH = os.environ.get("KA_CONTRACT") or os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("keepalive_contract", CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

m = _contract_module
Keepalive = m.Keepalive
gl = m.gl
Address = m.Address
u256 = m.u256

# Production floors are 1e12 wei; tests use tiny amounts.
Keepalive.MIN_EPOCH_FEE = u256(1)
Keepalive.MIN_BOND = u256(1)

UTC = _dt.timezone.utc
BASE = _dt.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
BASE_EPOCH = int(BASE.timestamp())
DAY = 86400


class Clock:
    """The contract's only clock, replaced for tests."""

    def __init__(self):
        self.now = BASE

    def reset(self):
        self.now = BASE

    def set(self, moment):
        self.now = moment

    def advance(self, seconds):
        self.now = self.now + _dt.timedelta(seconds=seconds)

    def set_epoch(self, epoch_seconds):
        self.now = _dt.datetime.fromtimestamp(epoch_seconds, UTC)


CLOCK = Clock()
Keepalive._now_utc = staticmethod(lambda: CLOCK.now)

CUSTOMER = "0x" + "11" * 20
PROVIDER = "0x" + "22" * 20
STRANGER = "0x" + "33" * 20
WATCHER = "0x" + "44" * 20
WATCHER2 = "0x" + "55" * 20

URL = "https://status.acme.dev/health.json"
EPOCH_SECONDS = DAY
EPOCHS = 3
FEE_PER_EPOCH = 1000
TOTAL = FEE_PER_EPOCH * EPOCHS
BOND_BPS = 5000
BOND = TOTAL * BOND_BPS // 10000
PENALTY_BPS = 5000
PENALTY = FEE_PER_EPOCH * PENALTY_BPS // 10000
K = 3
GAP = 300
WINDOW = 1200
MAX_BREACHES = 2
WARMUP = m.WARMUP_SECONDS
SETTLE_DELAY = m.SETTLE_DELAY_SECONDS


def make_contract():
    CLOCK.reset()
    reset_transfers()
    return Keepalive()


def set_caller(address_str):
    gl.message.sender_address = Address(address_str)


def reset_transfers():
    gl.evm.transfers.clear()


class PaymentRejected(gl.vm.UserError):
    """Raised by the test helper when a payable call was rejected and its
    GEN refunded to the sender (the contract does not revert in that case,
    so it can never keep GEN with no record of the sender)."""


def _state(contract):
    return (dict(contract.slas), int(contract.sla_count), int(contract.ended_count),
            int(contract.cancelled_count), int(contract.locked_total), dict(contract.prober_last))


def call_payable(contract, method_name, value, *args, **kwargs):
    """Invoke a payable method as if `value` wei were sent with the call.

    - The value is credited to the contract balance first; if the method
      raises (only possible with no GEN attached) the credit is rolled
      back, like a reverted transaction.
    - If the method returns a rejection, the helper checks that the exact
      value was credited back to the sender and that nothing else changed,
      then raises PaymentRejected so tests can assert the rejection."""
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    sender = str(gl.message.sender_address).lower()
    before_state = _state(contract)
    before_owed = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
    try:
        result = getattr(contract, method_name)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        raise
    finally:
        gl.message.value = u256(0)
    if isinstance(result, str) and result.startswith('{"refunded_wei"'):
        data = _json.loads(result)
        after_owed = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
        assert data["refunded_wei"] == str(int(value)), data
        assert after_owed - before_owed == int(value), (before_owed, after_owed, value)
        assert _state(contract) == before_state, "a rejected payable call changed state"
        raise PaymentRejected(data["rejected"])
    return result


def withdraw(contract, address):
    """Withdraw and keep the stub balance in step with the transfer."""
    set_caller(address)
    before = len(gl.evm.transfers)
    contract.withdraw()
    sent = sum(int(t["value"]) for t in gl.evm.transfers[before:])
    contract.balance = contract.balance - u256(sent)
    return sent


def expect_user_error(testcase, fn, *args, **kwargs):
    with testcase.assertRaises(gl.vm.UserError) as ctx:
        fn(*args, **kwargs)
    return str(ctx.exception)


def tag_for(sla_id, provider=PROVIDER):
    return f"keepalive:{sla_id}:{provider.lower()}"


def healthy_body(sla_id="sla_0", provider=PROVIDER, **extra):
    data = {"status": "ok", "sla": tag_for(sla_id, provider)}
    data.update(extra)
    return _json.dumps(data)


def down_body(sla_id="sla_0", provider=PROVIDER):
    return _json.dumps({"status": "down", "sla": tag_for(sla_id, provider)})


def default_args(**over):
    args = dict(
        provider_wallet=PROVIDER, service_name="Acme API", health_url=URL, check_mode="json",
        check_spec="status=ok", epoch_seconds=EPOCH_SECONDS, epochs=EPOCHS, bond_bps=BOND_BPS,
        penalty_bps=PENALTY_BPS, outage_probes=K, min_gap_seconds=GAP, window_seconds=WINDOW,
        max_breaches=MAX_BREACHES, status_feed="", feed_component="", cache_bust=1,
    )
    args.update(over)
    return args


ARG_ORDER = ["provider_wallet", "service_name", "health_url", "check_mode", "check_spec", "epoch_seconds",
             "epochs", "bond_bps", "penalty_bps", "outage_probes", "min_gap_seconds", "window_seconds",
             "max_breaches", "status_feed", "feed_component", "cache_bust"]


def create_sla(c, value=None, customer=CUSTOMER, **over):
    args = default_args(**over)
    epochs = args["epochs"]
    if value is None:
        ok = isinstance(epochs, int) and not isinstance(epochs, bool) and epochs > 0
        value = FEE_PER_EPOCH * epochs if ok else TOTAL
    set_caller(customer)
    return call_payable(c, "create_sla", value, *[args[k] for k in ARG_ORDER])


def get(c, sla_id):
    return _json.loads(c.get_sla(sla_id))


def accept_sla(c, sla_id, provider=PROVIDER, bond=None):
    if bond is None:
        bond = int(get(c, sla_id)["bond_amount"])
    set_caller(provider)
    return call_payable(c, "accept_sla", bond, sla_id)


def active_sla(c, **over):
    sla_id = create_sla(c, **over)
    accept_sla(c, sla_id)
    return sla_id


def start_of(c, sla_id):
    return get(c, sla_id)["start_epoch"]


def at(c, sla_id, epoch=0, offset=0):
    """Move the clock to `offset` seconds into epoch `epoch` of the agreement."""
    sla = get(c, sla_id)
    CLOCK.set_epoch(sla["start_epoch"] + epoch * sla["epoch_seconds"] + offset)


class Web:
    """A render replacement: the body (a string, a dict, or an Exception to
    raise) served for any URL. The cache-busting query parameter is
    recorded but otherwise ignored."""

    def __init__(self, body=None):
        self.body = body
        self.urls = []

    def render(self, url, mode="text"):
        self.urls.append(url)
        body = self.body
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise RuntimeError("404")
        return body if isinstance(body, str) else _json.dumps(body)


def probe(c, sla_id, body, caller=WATCHER, model=None, prompts=None):
    """Run one probe as `caller` against a served `body`. `model` is the
    answer the model returns in semantic mode (dict, Exception or None)."""
    web = Web(body)
    calls = prompts if prompts is not None else []

    def exec_prompt(prompt, response_format="json"):
        calls.append(prompt)
        if model is None:
            raise AssertionError("the model must not be called here")
        if isinstance(model, Exception):
            raise model
        return model

    set_caller(caller)
    with _patch.object(gl.nondet.web, "render", side_effect=web.render), \
            _patch.object(gl.nondet, "exec_prompt", side_effect=exec_prompt):
        raw = c.probe(sla_id)
    probe.last_web = web
    return _json.loads(raw)


def probe_down(c, sla_id, caller=WATCHER, body=None):
    return probe(c, sla_id, body if body is not None else down_body(sla_id), caller)


def probe_up(c, sla_id, caller=WATCHER, body=None):
    return probe(c, sla_id, body if body is not None else healthy_body(sla_id), caller)


def prove_outage(c, sla_id, epoch=0, offset=0, caller=WATCHER, gap=GAP, count=K):
    """Fire `count` DOWN probes `gap` seconds apart, starting `offset`
    seconds into `epoch`, all by `caller` (one wallet may probe once per
    gap). Returns the last result."""
    result = None
    for i in range(count):
        at(c, sla_id, epoch, offset + i * gap)
        result = probe_down(c, sla_id, caller)
    return result


def settle_time(c, sla_id, epoch):
    sla = get(c, sla_id)
    CLOCK.set_epoch(sla["start_epoch"] + (epoch + 1) * sla["epoch_seconds"] + SETTLE_DELAY + 1)


def settle_due(c, sla_id, caller=STRANGER):
    set_caller(caller)
    c.settle_due(sla_id)
    return get(c, sla_id)


def pending(c, address):
    return int(c.get_pending_withdrawal(address))


def accounting(c):
    return _json.loads(c.get_accounting())


def run_to_end(c, sla_id):
    """Settle every epoch of an agreement."""
    sla = get(c, sla_id)
    CLOCK.set_epoch(sla["end_epoch"] + SETTLE_DELAY + 1)
    return settle_due(c, sla_id)
