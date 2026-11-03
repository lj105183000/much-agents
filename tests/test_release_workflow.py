from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _release_workflow() -> dict:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-images.yml").read_text(encoding="utf-8")
    )
    # PyYAML 1.1 parses the `on:` key as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    workflow["triggers"] = triggers
    return workflow


def test_release_workflow_runs_on_version_tags_and_manual_dispatch() -> None:
    workflow = _release_workflow()
    assert workflow["triggers"]["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in workflow["triggers"]


def test_release_workflow_can_push_and_sign_keylessly() -> None:
    workflow = _release_workflow()
    job = workflow["jobs"]["publish"]
    assert job["permissions"]["packages"] == "write"
    assert job["permissions"]["id-token"] == "write"
    steps = job["steps"]
    names = [step.get("name", "") for step in steps]
    assert any("Build and push API image" == name for name in names)
    assert any("Build and push web image" == name for name in names)
    assert any("Sign images (keyless)" == name for name in names)
    assert any("Verify signatures and SBOM attestations" == name for name in names)

    run_scripts = "\n".join(
        step.get("run", "") for step in steps if isinstance(step.get("run"), str)
    )
    assert 'cosign sign --yes "$API_REF"' in run_scripts
    assert 'cosign sign --yes "$WEB_REF"' in run_scripts
    assert "cosign verify " in run_scripts
    assert "cosign verify-attestation " in run_scripts
    assert "token.actions.githubusercontent.com" in run_scripts
    assert "release-images.yml@refs/" in run_scripts


def test_release_workflow_publishes_to_ghcr_by_digest() -> None:
    workflow = _release_workflow()
    steps = workflow["jobs"]["publish"]["steps"]
    build_steps = [
        step for step in steps if str(step.get("uses", "")).startswith(
            "docker/build-push-action"
        )
    ]
    assert len(build_steps) == 2
    for step in build_steps:
        assert step["with"]["push"] is True
    run_scripts = "\n".join(
        step.get("run", "") for step in steps if isinstance(step.get("run"), str)
    )
    step_env = "\n".join(
        str(step.get("env", "")) for step in steps if isinstance(step.get("env"), dict)
    )
    assert "ghcr.io/" in run_scripts or any(
        "ghcr.io/" in str(step.get("with", {}).get("tags", "")) for step in build_steps
    )
    assert "${{ steps.api.outputs.digest }}" in step_env
    assert "${{ steps.web.outputs.digest }}" in step_env


def test_ci_no_longer_signs_local_images_with_static_keys() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "cosign sign" not in workflow
    assert "COSIGN_PRIVATE_KEY" not in workflow
    assert "CI_SIGN_IMAGES" not in workflow
