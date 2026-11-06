# Keepalive - portal submission

**Title:** Keepalive - self-settling service-level agreements

**Summary:** A customer prepays a service in epochs and the provider posts a bond. Anyone can probe the service; every validator fetches the health URL independently and a probe counts only if they all agree. A run of spaced failed probes proves an outage, refunds the epoch and pays a penalty from the bond, and a finder's fee rewards whoever proves it. Successful probes never erase failures, so a provider cannot cancel evidence by timing a probe. The provider proves it operates the endpoint by serving a tag that names the agreement and its wallet. An optional upstream status incident can excuse an outage, and an optional model check can only veto a healthy-looking page.

**What is different from a bounty or escrow dispute contract:** there is no judge and no jury. Settlement is a rule over independently verified observations, and time alone settles every agreement.

**Evidence to link:** GitHub repository, Pages site, release, contract explorer link, `TESTNET_TESTS.md`.
