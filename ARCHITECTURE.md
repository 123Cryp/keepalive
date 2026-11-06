# Keepalive - Architecture

## 1. Design goals

1. Decide every fact in code. Use a model only where code cannot, and never let it create evidence.
2. Make every settlement depend on observations that all validators made independently and agreed on.
3. Give no party a way to erase or fabricate evidence by choosing when to probe.
4. Make time alone settle every agreement: no state depends on a counterparty or a model answering.
5. Keep payouts pull-based so no settlement path can be blocked by a recipient.

## 2. Why there is no jury

A one-shot escrow needs a judge because someone must decide whether work was delivered. A service level is different: the evidence is a stream of cheap, repeatable, independently verifiable observations. Keepalive therefore replaces adjudication with a rule over observations. The cost of being wrong is bounded by one epoch's fee and the penalty, and every parameter is visible to the provider before it accepts.

## 3. The probe

`probe(sla_id)` is permissionless. The contract builds one context (URL with an optional cache-busting parameter, binding tag, check, transaction time) and runs `gl.eq_principle.strict_eq` over a pure function. Every validator fetches the URL and returns one short string:

| Result | Meaning | Effect |
| --- | --- | --- |
| `UP` | served, tag present, check passed | statistic only |
| `DOWN:<reason>` | unreachable, empty, no tag, wrong field, stale, criteria not met | evidence |
| `UNREADABLE:<reason>` | rate limit, unusable model answer, ungrounded quote | `probe` reverts, nothing recorded |

All validators must return exactly the same string; otherwise the probe fails and nothing is recorded. Errors are caught inside the closure and returned as strings, so no exception crosses the consensus call.

Check modes:

| Mode | Healthy when |
| --- | --- |
| `tag` | the response carries the binding tag |
| `json` | the tag is present, a top-level field equals the agreed value, and optionally a timestamp field is fresher than a limit |
| `semantic` | the tag is present and the model, given plain-language criteria, answers `meets: true` with a quote that exists verbatim in the page |

In semantic mode the model is a veto: it is consulted only after the code checks pass, a failing page can never be rescued, and an answer without a verifiable quote is `UNREADABLE`, which proves nothing either way.

## 4. The outage rule

State per agreement: `down_times` (counted failures still inside the window), `last_down_ts`, and per-epoch counters.

- A probe by a wallet is refused if that wallet probed less than `min_gap_seconds` ago.
- A `DOWN` is refused (the call reverts) if the previous counted `DOWN` is less than `min_gap_seconds` old, so failures are always spaced.
- A counted `DOWN` is added to `down_times`; entries older than `window_seconds` are dropped.
- When `down_times` holds `outage_probes` entries, an outage is proven for the epoch of the proving probe, its window `[first, last]` is stored, and `down_times` is cleared.
- `UP` increments statistics and nothing else.

### Why UP does not reset the chain

If a successful probe cancelled earlier failures, the provider could answer every customer probe with its own: serve a healthy response only around the moment its probe is validated, and the chain never reaches the threshold. By making `UP` inert, the only thing a provider can do with a probe is add information; it cannot subtract any. The price is that failed probes are spaced and bounded by a short window, so isolated failures spread over an epoch never add up.

### Why the gap is global for failures but per wallet for everything else

A global gap on all probes would let the provider monopolise every slot with its own probes and starve watchers. So the per-wallet cooldown applies to every probe, while the global spacing applies only to counted failures, which no one can profit from forging. Successful probes have a per-epoch budget so they cannot be used to bloat storage, and that budget never blocks a failed probe.

## 5. Endpoint control

The tag `keepalive:<sla id>:<provider wallet>` ties the endpoint to the agreement and to the wallet that posted the bond. A provider cannot nominate an unrelated, always-up URL. A customer cannot nominate a competitor's endpoint to fabricate failures, because the endpoint would have to carry a tag only the provider can know to publish; without it every probe is a failure, but the provider chose to accept the agreement with that URL after reading it.

## 6. Money

- The fee is split into equal epoch fees; creation fails unless the attached GEN divides evenly.
- Bond is `bond_bps` of the total fee. Penalty is `penalty_bps` of one epoch's fee, capped by what is left of the bond.
- The finder's fee is 10% of the penalty, paid to the first prover of the epoch, unless that is the provider.
- Counters `locked_total` (funds held for agreements) and `owed_total` (funds credited to withdrawers) are updated in exactly three places: `_lock`, `_release` and `withdraw`. The identity `balance == locked_total + owed_total` is exposed by `get_accounting` and asserted after every step of a randomised simulation.
- A payable call that is rejected while GEN is attached credits that GEN to the sender's withdrawable balance.

## 7. Settlement and exits

| Situation | Way out |
| --- | --- |
| Open, provider never accepts | `cancel_sla` (customer) |
| Active | `settle_due` after each epoch plus the settle delay, by anyone |
| Provider keeps failing | `terminate_for_cause` after `max_breaches` breached epochs |
| Provider wants out | `resign` |
| Nobody probes at all | the provider is paid; `settle_due` still works |
| Model or feed unavailable | nothing depends on them except an optional exemption, which can be retried |

`terminate_for_cause` and `resign` settle epochs that are already due first, so a good epoch is never refunded by mistake. Every remaining epoch with a proven outage is then charged as a breach on the spot, so a provider cannot resign during the settle delay to escape penalties it has already earned. Every other remaining epoch is refunded.

## 8. Force majeure

An agreement may declare a public Atlassian Statuspage feed (an allowlist of hosts, path `/api/v2/incidents.json`) and optionally a component. After an epoch with a proven outage ends, and before it settles, the provider may call `file_exemption`. Validators fetch the feed; the epoch is excused only if a `major` or `critical` incident (on the declared component when one was named) overlaps every proven outage window of that epoch. Pure code, no model. A feed that cannot be read reverts and can be retried; a feed that does not cover the outage denies the exemption for good.

## 9. Platform rules this contract follows

- The deploy file is two header lines then code, generated with `ast` and `tokenize` (never regex) and verified by comparing syntax trees.
- Storage types are class-level annotations; nothing assigns `{}` or `[]` to a storage container in `__init__`.
- Nondeterministic closures are module-level functions that never touch `self`.
- Errors such as a 404 are caught inside the closure and returned as plain values.
- Caller identity comes from `gl.message.sender_address`; time comes from a single clock function that reads the transaction timestamp.
- `exec_prompt(response_format="json")` is read as an already-parsed object.
- Every method that can fail after reading state does all its validation before its first write, and the offline stub rolls storage back on any exception, like a reverted transaction.

## 10. Known limitations (disclosed)

1. **Probe-aware providers.** A provider that can tell a validator's request from a user's can serve healthy responses only to probes. `UP` being inert stops it from erasing failures, but it can still make probes see `UP`. Point the health URL at a real user-facing path, keep the cache-busting parameter on, and use `json` freshness checks where possible.
2. **Nobody probes, nobody is paid for it.** If neither the customer nor a watcher probes during an outage, the provider is paid. The finder's fee exists to attract watchers; a customer who relies on this contract should run a watcher.
3. **Flapping endpoints defeat consensus.** If an endpoint answers differently to different validators, probes fail to reach agreement and record nothing. A provider could try to exploit that; a customer sees it as repeated reverted probes and can terminate only after enough settled breaches.
4. **Validator-side faults look like outages.** If every validator fails to reach an endpoint for a reason on their side, the probe is a failure. The outage rule needs several spaced failures to limit this.
5. **Semantic mode is the weakest mode.** Models can be wrong, and a provider controls the page the model reads. The veto-only design means the model cannot help a provider, but it can hurt one. Prefer `tag` or `json`.
6. **The tag must be visible text.** Pages are fetched as text, so an HTML comment or meta tag is not seen. Use a JSON or plain-text health endpoint.
7. **Epoch boundaries.** An outage is charged to the epoch of the probe that proves it, not the epoch it started in.
8. **Early exits close the exemption window.** Terminating or resigning charges unsettled outages at once, so an exemption that has not been filed yet can no longer be filed. Clean unsettled epochs, including the running one, are refunded in full rather than pro-rated.
9. **Exemption feed coverage.** Statuspage feeds list only recent incidents (around the last 50), so an exemption must be filed within the settle delay, which is when it matters anyway. The hosts are an allowlist inside the contract.
10. **Cache-busting is a convention.** The parameter `_ka` is appended; an endpoint that rejects unknown parameters needs `cache_bust` set to 0 and then may serve stale responses from a CDN.
11. **No pro-rated partial outages.** The unit is the epoch; an outage of ten minutes and one of ten hours cost the same.
12. **Sybil probers.** Many wallets can each probe once per gap. They cannot compress failures (global spacing) and cannot block others (no global cooldown for successes), but they can raise storage use up to the budgets.
13. **Clock.** All time checks use the transaction timestamp pinned by GenVM.
14. **Live coverage.** See `TESTNET_TESTS.md` for exactly what has and has not been run on GenLayer Studio. The exemption path is covered offline only: it needs a real incident on a real feed overlapping a proven outage.

## 11. Testing

Offline tests run against a self-written SDK stub that reproduces the real SDK's habits: empty storage containers, range-checked `u256`, a JSON round trip of every consensus result, and storage rollback when a write method raises. A controllable clock replaces the contract's single clock function. The suite includes AST audits (every public write is classified by who may call it, only `withdraw` moves GEN out, no nondeterministic closure references `self`), an adversarial test for each rule in section 4, and a seeded random simulation that checks the solvency identity after every step and that the contract ends empty with every wei paid out exactly once. The whole suite runs against both `contract.py` and `contract_deploy.py`. The stub does not simulate real networks, real models or multi-validator consensus.
