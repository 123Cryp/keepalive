# Keepalive

Service-level agreements that settle themselves, built as a GenLayer Intelligent Contract.

A customer prepays a service in epochs. The provider posts a bond. Anyone can probe the service: every GenLayer validator fetches the health URL on its own, and a probe counts only if they all classify the response identically. When enough probes fail inside a short window, the epoch is proven breached: its fee goes back to the customer, a penalty is taken from the bond, and whoever proved the outage earns a finder's fee. Nobody rules on anything, and time alone settles every agreement.

The central rule: **a monitored party must not be able to erase evidence by timing a probe of its own.** Successful probes are statistics. They never cancel failed ones.

## How it works

| Step | Who | What happens |
| --- | --- | --- |
| `create_sla` | Customer | Escrows the whole fee, anchors every term on-chain |
| `accept_sla` | Provider | Posts the bond; the clock starts after a short warm-up |
| `probe` | Anyone | Validators fetch the URL; UP or DOWN is recorded only if all agree |
| `file_exemption` | Provider | Optional: an upstream status incident excuses an epoch |
| `settle_due` | Anyone | Pays every finished epoch; ends the agreement after the last one |
| `terminate_for_cause` | Customer | After enough breached epochs, recovers everything unsettled |
| `resign` | Provider | Walks away: unsettled outages are still charged, plus one epoch's penalty |
| `withdraw` | Anyone | Pulls their own balance out |

## What is anchored on-chain at creation

| Anchor | Example |
| --- | --- |
| Provider wallet | `0x...` |
| Health URL | `https://acme.dev/health.json` (https, public DNS name only) |
| Healthy means | tag served, a JSON field, or plain-language criteria |
| Epochs | 3 epochs of 1 day |
| Money | total fee, provider bond (10% to 200%), penalty (0% to 100% of one epoch's fee) |
| Outage rule | failed probes needed, minimum gap, window |
| Termination | breached epochs that allow it |
| Upstream feed | optional public status feed and component |

The provider sees every term before it posts the bond.

## Proof that the provider runs the endpoint

A response is healthy only if it carries the binding tag `keepalive:<agreement id>:<provider wallet>`. Only the operator of the endpoint can publish it, so a provider cannot point an agreement at somebody else's always-up URL, and a customer cannot point it at a stranger's.

## The outage rule

An outage is proven when `outage_probes` failed probes fall inside `window_seconds`, with each counted failure at least `min_gap_seconds` after the previous one. The gap means an outage lasts at least `(probes - 1) x gap`; many wallets cannot compress it. Each wallet may also probe only once per gap. The first prover of an epoch earns 10% of its penalty.

## Outcomes per epoch

| Epoch | Customer receives | Provider receives | Finder receives |
| --- | --- | --- | --- |
| Clean | nothing | the epoch fee | nothing |
| Breached | fee + penalty - finder's fee | nothing | 10% of the penalty |
| Excused | the epoch fee | nothing, and no penalty | nothing |

After the last epoch the unused bond returns to the provider. All payouts are credited to a ledger and moved only by `withdraw`. The contract's balance always equals the funds locked in agreements plus the funds owed to withdrawers (`get_accounting`).

## Rules worth knowing

- **Unreadable is never evidence.** A rate limit, an unusable model answer or an ungrounded model quote makes `probe` revert and records nothing.
- **A failed fetch is a failed probe.** An endpoint that cannot be reached is not serving, and every validator must agree.
- **No probes means the provider is paid.** Customers and watchers must probe; the finder's fee pays watchers.
- **Time settles everything.** `settle_due` needs no probe, no model and no counterparty.
- **Rejected payments are refunded, not stranded.** A payable call that is rejected while GEN is attached credits that GEN to the sender's withdrawable balance and returns the reason.
- **The model can only veto.** In semantic mode a response must already pass the code checks; the model can then mark it failed, never rescue a failing one, and a pass needs a quote that exists verbatim in the page.

## Repository layout

| Path | Purpose |
| --- | --- |
| `contract.py` | Documented contract |
| `contract_deploy.py` | Generated file to paste into GenLayer Studio |
| `contract_testnet_deploy.py` | Same contract with short time windows, for live tests |
| `TESTNET_TESTS.md` | Step-by-step live test and record |
| `tools/make_deploy.py` | Builds the deploy file using `ast` and `tokenize` |
| `tools/make_testnet_deploy.py` | Builds the short-window copy |
| `index.html` | Single-file frontend (genlayer-js from esm.sh), hash-routed |
| `demo/health.json` | Example health file for a live test |
| `tests/` | Offline tests with a self-written SDK stub |
| `ARCHITECTURE.md` | Design, trust model and disclosed limitations |
| `PORTAL_SUBMISSION.md` | Text for the portal submission |

## Run the tests

No installs are needed.

```
python3 -m unittest discover -s tests
KA_CONTRACT=contract_deploy.py python3 -m unittest discover -s tests
python3 tools/make_deploy.py --check
```

## Deploy

1. Run `python3 tools/make_deploy.py` after any change to `contract.py`.
2. In GenLayer Studio, create a new contract file and paste the full content of `contract_deploy.py` (or `contract_testnet_deploy.py` for short windows). The file begins with exactly two header lines and has no comments or docstrings, which Studio requires.
3. Deploy with no constructor arguments.
4. Open `index.html?contract=<deployed address>` once. The address is remembered in the browser.

## Methods

Writes: `create_sla`, `accept_sla`, `cancel_sla`, `probe`, `file_exemption`, `settle_due`, `terminate_for_cause`, `resign`, `withdraw`.

Views: `get_sla`, `list_slas`, `get_stats`, `total_slas`, `get_binding_tag`, `get_pending_withdrawal`, `get_contract_balance`, `get_accounting`.

## Status

Deployed on GenLayer Studio (testnet build, short time constants).

| Item | Value |
| --- | --- |
| Contract | `0xE1d5E6c6a1F35219889CDdC3B53ddE927C0AF94A` |
| Deployer | `0xBD767E...da35F6` |
| Deploy transaction | `0x4878923d...e32e12fa` (finalized, accepted) |

Live test results go in `TESTNET_TESTS.md`.

## License

MIT
