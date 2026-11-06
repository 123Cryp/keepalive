# Keepalive - live test on GenLayer Studio

Deploy `contract_testnet_deploy.py`. It differs from `contract_deploy.py` only in four time constants: minimum epoch 300 s, warm-up 60 s, minimum gap 30 s, settle delay 300 s.

## Wallets

| Role | Address |
| --- | --- |
| Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` |
| Provider | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` |
| Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` |

## Health file

`demo/health.json` already carries the tag for `sla_0` and the provider wallet:

```
{"status": "ok", "sla": "keepalive:sla_0:0x72be82ad9d8800d45e5cc22b4f2c29bbedf73bc6"}
```

Health URL: `https://raw.githubusercontent.com/<account>/<repo>/main/demo/health.json`. If the new agreement is not `sla_0`, read the tag with `get_binding_tag` and put it in the file.

## create_sla arguments

| Argument | Value |
| --- | --- |
| provider_wallet | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` |
| service_name | `Keepalive demo` |
| health_url | the raw URL above |
| check_mode | `json` |
| check_spec | `status=ok` |
| epoch_seconds | `600` |
| epochs | `2` |
| bond_bps | `5000` |
| penalty_bps | `10000` |
| outage_probes | `2` |
| min_gap_seconds | `30` |
| window_seconds | `120` |
| max_breaches | `1` |
| status_feed | empty |
| feed_component | empty |
| cache_bust | `1` |

## Steps

| Step | Wallet | Address | Method | Value | Expected |
| --- | --- | --- | --- | --- | --- |
| 1 | Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` | `create_sla` (table above) | 20 GEN | `sla_0`, 10 GEN per epoch, bond 10 GEN |
| 2 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `accept_sla("sla_0")` | 10 GEN | rejected, 10 GEN credited back to the watcher |
| 3 | Provider | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` | `accept_sla("sla_0")` | 10 GEN | `active` |
| 4 | Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` | `probe("sla_0")` after 60 s | 0 | `UP` |
| 5 | Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` | `probe("sla_0")` at once | 0 | revert: probed too recently |
| 6 | Provider | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` | edit `demo/health.json`: `"status": "down"` | - | - |
| 7 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `probe("sla_0")` | 0 | `DOWN`, `counted_downs` 1 |
| 8 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `probe("sla_0")` 30 s later | 0 | `DOWN`, `outage_proven` true |
| 9 | Provider | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` | edit `demo/health.json`: `"status": "ok"` | - | - |
| 10 | Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` | `probe("sla_0")` in epoch 2 | 0 | `UP` |
| 11 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `settle_due("sla_0")` 27 min after step 3 | 0 | `ended`, epoch 1 `breach`, epoch 2 `clean` |
| 12 | Customer | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` | `withdraw()` | 0 | 19 GEN |
| 13 | Provider | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` | `withdraw()` | 0 | 10 GEN |
| 14 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `withdraw()` | 0 | 11 GEN (10 GEN refund + 1 GEN finder's fee) |
| 15 | Watcher | `0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128` | `get_accounting()` | - | balance 0, solvent true |

If the outage is proven after the first 600 s, it is charged to epoch 2 instead; the totals are the same.

## Record

Fill in after running.

| Path | Transaction | Result |
| --- | --- | --- |
| create | | |
| wrong wallet accepts (refund) | | |
| accept | | |
| UP probe | | |
| probe before the gap | | |
| DOWN probes, outage proven | | |
| settle_due | | |
| withdraw x3 | | |
| accounting back to zero | | |
| exemption path | - | offline only (needs a real incident on a real feed) |

## Phase A result (GenLayer Studio, 7 Nov 2026)

Contract `0xE1d5E6c6a1F35219889CDdC3B53ddE927C0AF94A`, health URL `https://example.com/health.json` in `tag` mode, so every probe was a failure.

| Path | Result |
| --- | --- |
| `create_sla` (20 GEN, 2 epochs of 300 s) | `sla_0` created |
| Accept by a wallet that is not the provider | rejected, 1 GEN credited back, withdrawn |
| Accept by the provider (10 GEN bond) | `active` |
| Probe before the first epoch | reverted as expected |
| First failed probe | `DOWN:unreachable`, 1 counted |
| Probe right after | reverted: probed too recently |
| Second failed probe 30 s later | outage proven, first prover recorded as finder |
| `settle_due` before the delay | reverted as expected |
| `settle_due` after epoch 1 plus delay | epoch 1 settled as breach |
| `settle_due` after epoch 2 plus delay | agreement ended |
| `withdraw` by customer, provider, watcher | 19, 10 and 1 GEN |
| `get_accounting` | balance 0, locked 0, owed 0, solvent true |

Phase B (healthy `UP` probes and the tag on a real file) is run after the repository is public.

## Phase A2 result (GenLayer Studio, 7 Nov 2026)

Same contract, same failing health URL, three more agreements.

| Path | Result |
| --- | --- |
| `cancel_sla` by a wallet that is not the customer | reverted |
| `cancel_sla` by the customer before acceptance | `cancelled`, fee credited back |
| `create_sla` with the provider set to the customer's own wallet | rejected, 20 GEN credited back |
| `accept_sla` with no GEN attached | reverted |
| `resign` by a wallet that is not the provider | reverted |
| `resign` by the provider with an unsettled proven outage | epoch 1 `breach` (10 GEN penalty, finder's fee paid), epoch 2 `refunded`, bond fully forfeited, status `resigned` |
| `withdraw` with no balance | reverted |
| `withdraw` by customer and watcher | 69 and 1 GEN |
| `get_accounting` | balance 0, locked 0, owed 0, solvent true |

## Phase B result (GenLayer Studio, 7 Nov 2026)

Same contract, `sla_3`: `json` mode (`status=ok`) against `https://raw.githubusercontent.com/123Cryp/keepalive/main/demo/health.json`, two epochs of 600 s, two failed probes needed, minimum gap 30 s.

| Path | Result |
| --- | --- |
| `get_binding_tag` | matched the tag served in `demo/health.json` |
| Probe by the customer and by a watcher on the healthy file | `UP` twice, `up_count` 2 |
| File edited to `"status": "down"`, probe by the watcher | `DOWN:field` |
| Second probe 63 s later | outage proven, watcher recorded as finder, earlier `UP` probes kept in the statistics and did not cancel the evidence |
| File still `down` when the customer probed in epoch 2 | one `DOWN:field`, no outage (a single failure is not an outage) |
| File restored, probe in epoch 2 | `UP` |
| `settle_due` after epoch 1 plus delay | epoch 1 `breach` |
| `settle_due` after epoch 2 plus delay | `ended` |
| `withdraw` by customer, provider, watcher | 19, 10 and 1 GEN |
| `get_accounting` | balance 0, locked 0, owed 0, solvent true |
| `get_stats` | total 4, ended 3, cancelled 1, live 0 |

Not covered live: the status-feed exemption path, `semantic` mode and `terminate_for_cause`. They are covered offline only.
