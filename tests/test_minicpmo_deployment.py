"""Tests for MiniCPM-o deployment profile selection."""

from interactformer.deployment.minicpmo import (
    GPUInfo,
    assess_minicpmo_environment,
    parse_nvidia_smi,
)


def test_parse_nvidia_smi_and_select_official_profile():
    gpus = parse_nvidia_smi("NVIDIA L40S, 46068, 570.124.06\n")
    report = assess_minicpmo_environment(
        gpus,
        disk_free_gb=120,
        docker_available=True,
        compose_available=True,
        linux=True,
    )
    assert report.profile == "official-full-duplex"
    assert report.ready is True


def test_4090_is_explicitly_experimental_not_rejected():
    report = assess_minicpmo_environment(
        [GPUInfo("NVIDIA GeForce RTX 4090", 24564, "572.61")],
        disk_free_gb=100,
        docker_available=True,
        compose_available=True,
        linux=True,
    )
    assert report.profile == "4090-experimental"
    assert report.ready is True
    assert any("官方 >28GB" in warning for warning in report.warnings)


def test_missing_runtime_and_disk_are_blockers():
    report = assess_minicpmo_environment(
        [],
        disk_free_gb=20,
        docker_available=False,
        compose_available=False,
        linux=False,
    )
    assert report.profile == "unsupported"
    assert report.ready is False
    assert len(report.errors) >= 4
