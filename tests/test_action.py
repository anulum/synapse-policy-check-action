# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE policy check — the Action installs only the pinned, hash-verified Core release

"""Tests for the composite Action in ``action.yml``.

The contract tests pin what can drift silently: inputs reach bash only through
``env``, every input is consumed, the lock pins exactly one Core release by
hash, and the attestation constants name that same release.

The end-to-end tests run the Action's own bash steps, in order, the way a
GitHub runner does (``bash --noprofile --norc -eo pipefail``), with the
runner's ``RUNNER_TEMP``, ``GITHUB_ACTION_PATH`` and ``GITHUB_OUTPUT``
contract. They download the real release from PyPI by hash, verify its real
GitHub build attestation with the GitHub CLI, install it into an isolated
environment and run the real ``synapse policy-check``.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
LOCK = ROOT / "requirements.txt"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORE = "synapse-channel"


def _action() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(ACTION.read_text(encoding="utf-8")))


def _steps() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", _action()["runs"]["steps"])


def _run_steps() -> list[dict[str, Any]]:
    return [step for step in _steps() if "run" in step]


def _step(name: str) -> dict[str, Any]:
    return next(step for step in _steps() if step["name"] == name)


def _lock_entries() -> dict[str, list[tuple[str, list[str]]]]:
    """Map each locked distribution to its (version, hashes) entries."""
    entries: dict[str, list[tuple[str, list[str]]]] = {}
    text = LOCK.read_text(encoding="utf-8")
    for block in re.split(r"\n(?=[a-z])", text):
        lines = [line for line in block.splitlines() if not line.startswith("#")]
        if not lines:
            continue
        head = re.match(r"([a-z0-9-]+)==([^ ;\\]+)", lines[0])
        assert head is not None, lines[0]
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", "\n".join(lines))
        entries.setdefault(head[1], []).append((head[2], hashes))
    return entries


def _core_pin() -> tuple[str, list[str]]:
    [(version, hashes)] = _lock_entries()[CORE]
    return version, hashes


# -- contract -----------------------------------------------------------------


def test_the_action_is_composite_with_required_inputs() -> None:
    action = _action()
    assert action["runs"]["using"] == "composite"
    assert len(action["description"]) < 125
    inputs = action["inputs"]
    for name in ("task", "policy", "receipt-json"):
        assert inputs[name]["required"] is True, name
    for name in (
        "enforce",
        "merkle-db",
        "trusted-signing-keys",
        "verify-attestation",
        "python-version",
    ):
        assert inputs[name]["required"] is False, name
    assert inputs["enforce"]["default"] == "true"
    assert inputs["verify-attestation"]["default"] == "false"
    assert "version" not in inputs  # the Core release is pinned by this Action's own version


def test_run_bodies_never_interpolate_expressions() -> None:
    """Values reach bash through env, never via ``${{ }}`` in the script."""
    for step in _run_steps():
        assert "${{" not in step["run"], step["name"]
        assert step["shell"] == "bash"


def test_every_input_is_consumed() -> None:
    consumed: set[str] = set()
    for step in _steps():
        for value in list(step.get("env", {}).values()) + list(step.get("with", {}).values()):
            consumed.update(re.findall(r"inputs\.([a-z-]+)", str(value)))
    assert consumed == set(_action()["inputs"])


def test_the_only_external_action_is_pinned_by_commit() -> None:
    uses = [step["uses"] for step in _steps() if "uses" in step]
    assert len(uses) == 1
    assert re.fullmatch(r"actions/setup-python@[0-9a-f]{40}", uses[0])
    # the job's own Python stays untouched: the check runs from an isolated venv
    assert _step("Set up Python")["with"]["update-environment"] is False


def test_every_pip_call_requires_hashes_and_no_dependency_resolution() -> None:
    pip_calls = [
        call
        for step in _run_steps()
        for call in re.findall(
            r"-m pip (?:download|install)[^\n]*", re.sub(r"\\\n\s*", " ", step["run"])
        )
    ]
    assert len(pip_calls) == 2
    for call in pip_calls:
        assert "--require-hashes" in call
        assert "--no-deps" in call
        assert '-r "$GITHUB_ACTION_PATH/requirements.txt"' in call
    download, install = pip_calls
    assert "--only-binary=:all:" in download
    assert "--no-index" in install  # the install reads only the verified download


def test_the_lock_pins_every_file_by_hash() -> None:
    entries = _lock_entries()
    assert set(entries) == {CORE, "websockets"}
    for name, versions in entries.items():
        for version, hashes in versions:
            assert re.fullmatch(r"[0-9][0-9a-z.]*", version), (name, version)
            assert hashes, (name, version)
    version, hashes = _core_pin()
    assert len(hashes) == 2  # the wheel and the sdist
    assert (ROOT / "requirements.in").read_text(encoding="utf-8") == f"{CORE}=={version}\n"


def test_the_attestation_constants_name_the_locked_release() -> None:
    env = _step("Verify the synapse-channel build attestation")["env"]
    version, _hashes = _core_pin()
    assert env["CORE_TAG"] == f"v{version}"
    assert re.fullmatch(r"[0-9a-f]{40}", env["CORE_COMMIT"])
    assert env["CORE_REPOSITORY"] == "anulum/synapse-channel"
    assert env["GH_TOKEN"].replace(" ", "") == "${{github.token}}"  # the job token, never a literal


def test_the_report_output_keeps_the_cli_exit_status() -> None:
    action = _action()
    assert action["outputs"]["report"]["value"] == "${{ steps.check.outputs.report }}"
    check = _step("Run synapse policy-check")
    assert "GITHUB_OUTPUT" in check["run"]
    assert 'exit "$status"' in check["run"]
    assert 'args+=(--trusted-signing-key "$key")' in check["run"]


# -- published release --------------------------------------------------------


def _get_json(url: str) -> Any:
    """GET a JSON document over HTTPS only, without redirects."""
    parts = urllib.parse.urlsplit(url)
    assert parts.scheme == "https", url
    connection = http.client.HTTPSConnection(parts.netloc, timeout=60)
    try:
        connection.request(
            "GET",
            f"{parts.path}?{parts.query}" if parts.query else parts.path,
            headers={"Accept": "application/json", "User-Agent": "synapse-policy-check-tests"},
        )
        response = connection.getresponse()
        assert response.status == 200, (url, response.status)
        return json.loads(response.read())
    finally:
        connection.close()


def test_the_lock_hashes_are_the_published_release_files() -> None:
    version, hashes = _core_pin()
    release = _get_json(f"https://pypi.org/pypi/{CORE}/{version}/json")
    published = {item["packagetype"]: item["digests"]["sha256"] for item in release["urls"]}
    assert set(published) == {"bdist_wheel", "sdist"}
    assert sorted(published.values()) == sorted(hashes)


def test_the_attested_commit_is_the_release_tag() -> None:
    env = _step("Verify the synapse-channel build attestation")["env"]
    ref = _get_json(
        f"https://api.github.com/repos/{env['CORE_REPOSITORY']}/git/ref/tags/{env['CORE_TAG']}"
    )
    target = ref["object"]
    if target["type"] == "tag":  # an annotated tag points at its tag object first
        target = _get_json(target["url"])["object"]
    assert target["type"] == "commit"
    assert target["sha"] == env["CORE_COMMIT"]


# -- end to end ---------------------------------------------------------------


@dataclass
class StepResult:
    """What one bash step returned and wrote to ``GITHUB_OUTPUT``."""

    code: int
    stdout: str
    stderr: str
    outputs: dict[str, str]


def _parse_outputs(text: str) -> dict[str, str]:
    outputs: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, marker = line.split("<<", 1)
            body = []
            for inner in lines:
                if inner == marker:
                    break
                body.append(inner)
            outputs[key] = "\n".join(body)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return outputs


class Runner:
    """Run the Action's bash steps against one runner temp directory."""

    def __init__(self, temp: Path) -> None:
        self.temp = temp
        self.base_env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", str(temp)),
            "RUNNER_TEMP": str(temp),
            "GITHUB_ACTION_PATH": str(ROOT),
        }

    def run(self, name: str, env: Mapping[str, str]) -> StepResult:
        """Run the named step's bash body with the runner contract and ``env``."""
        step = _step(name)
        output = self.temp / f"output-{len(list(self.temp.glob('output-*')))}"
        output.write_text("", encoding="utf-8")
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            env={**self.base_env, **env, "GITHUB_OUTPUT": str(output)},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return StepResult(
            result.returncode,
            result.stdout,
            result.stderr,
            _parse_outputs(output.read_text(encoding="utf-8")),
        )


def _gh_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    gh = shutil.which("gh")
    assert gh is not None, "the attestation tests need the GitHub CLI"
    return subprocess.run(
        [gh, "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


@dataclass
class Installed:
    """The runner state after the download and install steps."""

    runner: Runner
    wheels: str
    synapse: str


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Installed]:
    runner = Runner(tmp_path_factory.mktemp("runner"))
    downloaded = runner.run(
        "Download the pinned synapse-channel release", {"BASE_PYTHON": sys.executable}
    )
    assert downloaded.code == 0, downloaded.stderr
    wheels = downloaded.outputs["wheels"]
    install = runner.run(
        "Install synapse-channel into an isolated environment",
        {"BASE_PYTHON": sys.executable, "WHEELS": wheels},
    )
    assert install.code == 0, install.stderr
    yield Installed(runner, wheels, install.outputs["synapse"])


def _attestation_env(installed: Installed, **overrides: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in _step("Verify the synapse-channel build attestation")["env"].items()
        if "${{" not in value
    }
    env.update(WHEELS=installed.wheels, GH_TOKEN=_gh_token(), VERIFY_ATTESTATION="true")
    env.update(overrides)
    return env


def test_the_download_holds_only_the_locked_files(installed: Installed) -> None:
    version, hashes = _core_pin()
    files = sorted(path.name for path in Path(installed.wheels).iterdir())
    assert files[0] == f"synapse_channel-{version}-py3-none-any.whl"
    assert [name.split("-")[0] for name in files] == ["synapse_channel", "websockets"]
    assert all(name.endswith(".whl") for name in files)


def test_the_isolated_install_is_the_pinned_release(installed: Installed) -> None:
    version, _hashes = _core_pin()
    python = Path(installed.synapse).parent / "python"
    shown = subprocess.run(
        [str(python), "-c", "import synapse_channel; print(synapse_channel.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shown.stdout.strip() == version
    assert str(installed.runner.temp) in installed.synapse


def test_the_real_attestation_verifies(installed: Installed) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation", _attestation_env(installed)
    )
    assert result.code == 0, result.stderr


def test_an_attestation_from_another_tag_is_refused(installed: Installed) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation",
        _attestation_env(installed, CORE_TAG="v0.99.31"),
    )
    assert result.code != 0


def test_an_attestation_from_another_commit_is_refused(installed: Installed) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation",
        _attestation_env(installed, CORE_COMMIT="0" * 40),
    )
    assert result.code != 0


def test_the_attestation_step_is_skipped_unless_requested(installed: Installed) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation",
        _attestation_env(installed, VERIFY_ATTESTATION="false", GH_TOKEN=""),
    )
    assert result.code == 0
    assert "hash-verified" in result.stdout


def test_an_unknown_attestation_switch_fails_closed(installed: Installed) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation",
        _attestation_env(installed, VERIFY_ATTESTATION="yes"),
    )
    assert result.code == 2
    assert "must be" in result.stderr


def test_the_attestation_step_needs_exactly_one_core_wheel(
    installed: Installed, tmp_path: Path
) -> None:
    result = installed.runner.run(
        "Verify the synapse-channel build attestation",
        _attestation_env(installed, WHEELS=str(tmp_path)),
    )
    assert result.code == 1
    assert "found 0" in result.stderr


def _check(installed: Installed, receipt: str, **overrides: str) -> StepResult:
    env = {
        "SYNAPSE": installed.synapse,
        "CHECK_TASK": "T1",
        "CHECK_POLICY": str(FIXTURES / "policy.json"),
        "CHECK_RECEIPT": str(FIXTURES / receipt),
        "CHECK_ENFORCE": "true",
        "CHECK_MERKLE_DB": "",
        "CHECK_TRUSTED_KEYS": "",
    }
    env.update(overrides)
    return installed.runner.run("Run synapse policy-check", env)


def test_a_passing_receipt_passes_and_reports(installed: Installed) -> None:
    result = _check(installed, "receipt-pass.json")
    assert result.code == 0, result.stderr
    report = json.loads(result.outputs["report"])
    assert json.loads(result.stdout) == report
    assert report["overall"] == "pass"
    assert report["blocked"] is False


def test_a_failing_receipt_gates_the_job_when_enforced(installed: Installed) -> None:
    result = _check(installed, "receipt-fail.json")
    assert result.code == 1
    report = json.loads(result.outputs["report"])
    assert report["overall"] == "fail"
    assert report["blocked"] is True


def test_an_advisory_run_reports_without_gating(installed: Installed) -> None:
    result = _check(installed, "receipt-fail.json", CHECK_ENFORCE="false")
    assert result.code == 0
    assert json.loads(result.outputs["report"])["overall"] == "fail"


def test_each_trusted_key_line_reaches_the_cli(installed: Installed, tmp_path: Path) -> None:
    missing = tmp_path / "absent.pub"
    result = _check(
        installed,
        "receipt-pass.json",
        CHECK_TRUSTED_KEYS=f"\n{missing}\n",
        CHECK_ENFORCE="false",
    )
    # the CLI received the key path: it refuses the unreadable key by name
    assert result.code == 2
    assert str(missing) in result.stdout + result.stderr
