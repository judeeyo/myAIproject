# pykorail dependency provenance

The deleted PyPI requirement is replaced by a pinned public preservation fork:

- Source: https://github.com/ArchsouL/pykorail
- Commit: `030576a6b41d874f35ebdf6b2d68ed76e70a1af7`
- The same HEAD was observed at https://github.com/jja6312/pykorail.
- Original project: https://github.com/devgyurak/pykorail (unavailable when checked).
- License: pykorail License, copyright 2026 devgyurak and contributors. The package
  includes its license. Commercial gain is prohibited; personal travel bookings
  and travel for an organization's own members are permitted. Consult the full
  dependency LICENSE before redistribution or commercial use.

This source has DynaPath SDK 2.0.1 support and preserves `AppVersion=7.0.10` and
`Version=250722001`. We have not guessed replacements for these protocol values.
Its generated package version is `0.0.1.dev253+g030576a6b` because release tags are
not present in the fork; the full commit is the identity used for installation.
This is not a claim that it is the deleted PyPI 0.6.1 distribution.

Installation and 27 tests with the real library pass. HTTP responses in tests
are fixtures. Separately, a live CLI --check succeeded on 2026-11-04 13:00 KST:
station master and 10 Seoul-to-Dongdaegu trains for 2026-11-06 were returned.
The previously blocked smart.letskorail.com domain is now reachable through the
configured proxy. No login, reservation or payment was attempted; these remain
unverified. Environment publication and restoration in a fresh task are also
unverified despite the current instance's successful real query.
