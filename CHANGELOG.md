<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Changelog

## [Unreleased]

### Added

- The `SYNAPSE policy check` composite Action, moved out of the SYNAPSE CHANNEL repository so it can
  be listed on its own. It keeps the inputs and the `report` output of the in-repo Action, with
  these changes:
  - **Pinned release.** It installs the pinned SYNAPSE CHANNEL 0.99.35 from a hash lock
    (`--require-hashes`, binary only, installed with `--no-index` from the verified download).
    The `version` input is gone: this Action's version decides the SYNAPSE CHANNEL version.
  - **Isolated environment.** The check runs from its own virtual environment. `setup-python` no
    longer changes the job's Python or `PATH`.
  - **Opt-in attestation check.** `verify-attestation: "true"` verifies the wheel's GitHub build
    attestation against the release workflow, tag and commit, on GitHub-hosted runners only.
