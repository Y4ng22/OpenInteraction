"""Pure deployment assessment logic for MiniCPM-o 4.5."""

from dataclasses import asdict, dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class GPUInfo:
    name: str
    memory_total_mb: int
    driver_version: str


@dataclass
class EnvironmentAssessment:
    profile: str
    ready: bool
    gpus: list[GPUInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "profile": self.profile,
            "ready": self.ready,
            "gpus": [asdict(gpu) for gpu in self.gpus],
            "errors": self.errors,
            "warnings": self.warnings,
            "recommendations": self.recommendations,
        }


def parse_nvidia_smi(output: str) -> list[GPUInfo]:
    gpus = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.rsplit(",", 2)]
        if len(parts) != 3:
            raise ValueError(f"unexpected nvidia-smi row: {line}")
        name, memory, driver = parts
        try:
            memory_mb = int(memory)
        except ValueError as exc:
            raise ValueError(f"invalid GPU memory value: {memory}") from exc
        gpus.append(GPUInfo(name, memory_mb, driver))
    return gpus


def assess_minicpmo_environment(
    gpus: Iterable[GPUInfo],
    *,
    disk_free_gb: float,
    docker_available: bool,
    compose_available: bool,
    linux: bool,
) -> EnvironmentAssessment:
    gpus = list(gpus)
    errors: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    if not linux:
        errors.append("官方完整 PyTorch 全双工服务要求 Linux；Windows 请使用 WSL2 或远程 Linux。")
    if not gpus:
        errors.append("未检测到 NVIDIA GPU。")
        profile = "unsupported"
    else:
        largest = max(gpus, key=lambda gpu: gpu.memory_total_mb)
        if largest.memory_total_mb >= 28 * 1024:
            profile = "official-full-duplex"
        elif largest.memory_total_mb >= 23 * 1024:
            profile = "4090-experimental"
            warnings.append(
                "显存低于官方 >28GB 要求；可在 4090 上实验，但需要单会话并避免额外 GPU 进程。"
            )
        elif largest.memory_total_mb >= 12 * 1024:
            profile = "quantized-cpp-only"
            warnings.append("显存只适合 llama.cpp-omni/GGUF 路线，不适合官方 PyTorch 后端。")
        else:
            profile = "unsupported"
            errors.append("GPU 显存不足 12GB。")

        for gpu in gpus:
            major = _driver_major(gpu.driver_version)
            if major is not None and major < 570:
                warnings.append(
                    f"{gpu.name} 驱动 {gpu.driver_version} 低于建议的 570+；cu128 可能依赖兼容模式。"
                )

    if disk_free_gb < 40:
        errors.append("可用磁盘不足 40GB，无法容纳权重和最小运行层。")
    elif disk_free_gb < 80:
        warnings.append("建议至少预留 80GB，覆盖 20GB 权重、镜像层和编译缓存。")
    if not docker_available:
        errors.append("未检测到 Docker Engine。")
    elif not compose_available:
        errors.append("未检测到 Docker Compose v2 插件。")

    if profile == "4090-experimental":
        recommendations.extend([
            "使用 SDPA 首次启动，确认显存后再启用 torch.compile。",
            "只启动一个 worker，关闭 vLLM 和桌面占显存程序。",
        ])
    elif profile == "official-full-duplex":
        recommendations.extend([
            "每张 GPU 只部署一个 worker-backend。",
            "4090/A100 上启用 torch.compile 以降低一秒处理单元延迟。",
        ])
    if profile in ("4090-experimental", "official-full-duplex"):
        recommendations.append("使用官方 PyTorch Compose，不要与 vLLM 安装在同一 Python 环境。")

    return EnvironmentAssessment(
        profile=profile,
        ready=not errors,
        gpus=gpus,
        errors=errors,
        warnings=warnings,
        recommendations=recommendations,
    )


def _driver_major(version: str):
    try:
        return int(version.split(".", 1)[0])
    except (TypeError, ValueError):
        return None
