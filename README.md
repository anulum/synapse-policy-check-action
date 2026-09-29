<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE policy check

A GitHub Action that checks a [SYNAPSE CHANNEL](https://github.com/anulum/synapse-channel)
release receipt against a policy file. It can also check the receipt's Merkle commitment and
signature.

This Action installs one exact, hash-verified SYNAPSE CHANNEL release.

## Usage

```yaml
permissions:
  contents: read
  attestations: read   # only needed with verify-attestation: "true"

steps:
  - uses: actions/checkout@<commit sha>
  - uses: anulum/<this repository>@<commit sha>   # pin by commit
    with:
      task: RELEASE-42
      policy: .synapse/policy.json
      receipt-json: release-receipt.json
      verify-attestation: "true"
```

| Input | Required | Default | Meaning |
| --- | --- | --- | --- |
| `task` | yes | | Task id / subject label for the decision report. |
| `policy` | yes | | Policy file (`.json`; `.toml` on Python 3.11+). |
| `receipt-json` | yes | | Release receipt from `synapse release --receipt-json`. |
| `enforce` | no | `"true"` | Fail the step when an enforcement-mode policy has a failing rule. `"false"` reports only. |
| `merkle-db` | no | `""` | Hub event store; recompute the receipt's coordination-log commitment against it. |
| `trusted-signing-keys` | no | `""` | Trusted hub `.pub` keys, one path per line; require a valid signature over the commitment. |
| `verify-attestation` | no | `"false"` | Also verify the GitHub build attestation of the SYNAPSE CHANNEL wheel before installing it. |
| `python-version` | no | `"3.12"` | Python used to run the check (3.10 or later). |

The output `report` holds the JSON decision report. The step's exit status is that of
`synapse policy-check`.

## What is installed, and how it is checked

- **Exact release.** `requirements.txt` pins `synapse-channel` and its only dependency,
  `websockets`, to exact versions. Every file is pinned by SHA-256.
  - The Action downloads with `--require-hashes --only-binary=:all:`.
  - It installs with `--no-index` from that verified download only.
  - Any change on the index fails the step instead of installing something else.
  - This Action's version decides the SYNAPSE CHANNEL version. There is no input to change it.
- **Isolated.** The check runs from its own virtual environment under `RUNNER_TEMP`.
  `setup-python` runs with `update-environment: false`, so your job's Python and `PATH` are
  unchanged.
- **Attestation (opt-in).** With `verify-attestation: "true"`, the GitHub CLI checks the wheel's
  build attestation (`gh attestation verify`) before the install. It must have been:
  - built by `anulum/synapse-channel/.github/workflows/publish.yml`;
  - from the pinned release tag and commit;
  - on a GitHub-hosted runner.

  The step needs the GitHub CLI (preinstalled on GitHub-hosted runners) and a token that can read
  attestations; the job token can, with `attestations: read`. Any value other than `"true"` or
  `"false"` fails the step. Attestation checking is planned to become the default at the next
  major version.
- **No injection path.** Inputs reach the shell only through `env`, never through `${{ }}` inside
  a script.

## Pinned release

| Action | SYNAPSE CHANNEL | Wheel SHA-256 | Source commit |
| --- | --- | --- | --- |
| unreleased | 0.99.32 | `1736df59595d3f4dc609240ac242239c3a926f5db1c60d0eeed0b4662cc92f88` | `261ebdfa441264f8206f876bea686146b34da62d` |

## Maintaining the pin

1. Set the new version in `requirements.in` and regenerate the lock:
   `uv pip compile requirements.in --universal --generate-hashes --python-version 3.10 --no-header --no-annotate -o requirements.txt`
   (keep the two comment lines at the top).
2. Set `CORE_TAG` and `CORE_COMMIT` in `action.yml` to the release tag and the commit it points at.
3. Run `pytest`. The tests check:
   - the lock hashes against PyPI;
   - the commit against the GitHub tag;
   - the real attestation, including two negative controls;
   - the Action's steps end to end.

## Licence

AGPL-3.0-or-later, with a commercial licence available: see `LICENSE`, `LICENSES/` and
`COMMERCIAL-LICENSE.md`.
