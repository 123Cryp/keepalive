# Changelog

## v1.1.0

Contract
- `list_slas_for(address, offset, limit)`: agreements of one wallet, as customer or provider.
- `get_profile(address)`: provider and customer track record from settled epochs, including `uptime_bps`.
- `get_sla_status(sla_id)`: running epoch, settlement countdown, failed probes in window, termination right.
- New storage `party_index`; no change to any money path or to existing method signatures.

Frontend
- New Record page (`#/profile/<address>`): uptime bar, earned and paid amounts, the wallet's agreements. Every address in an agreement links to it.
- New Live position card on running agreements.
- The epochs table scrolls sideways on narrow phones instead of overflowing.

Tests
- 169 to 185 tests, all green on `contract.py`, `contract_deploy.py` and `contract_testnet_deploy.py`.

## v1.0.0

Initial release: probe-evidence SLA contract, pull-payment ledger, frontend, live Studio tests.
