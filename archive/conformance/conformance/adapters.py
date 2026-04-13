from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AdapterSpec:
    name: str
    command: list[str]
    capabilities: set[str]
    prepare: list[str] | None
    env: dict[str, str]
    arg_order: str


def _expand_parts(parts: list[str], mapping: dict[str, str]) -> list[str]:
    return [part.format(**mapping) for part in parts]


def load_adapter_specs(config_path: Path) -> dict[str, AdapterSpec]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    workspace = str(config_path.resolve().parents[2])
    repo_root = str(config_path.resolve().parents[1])
    mapping = {
        "workspace": workspace,
        "repo_root": repo_root,
    }
    result: dict[str, AdapterSpec] = {}
    for name, entry in raw["implementations"].items():
        env = {key: value.format(**mapping) for key, value in entry.get("env", {}).items()}
        result[name] = AdapterSpec(
            name=name,
            command=_expand_parts(entry["command"], mapping),
            capabilities=set(entry.get("capabilities", [])),
            prepare=_expand_parts(entry["prepare"], mapping) if entry.get("prepare") else None,
            env=env,
            arg_order=str(entry.get("arg_order", "flags_then_extra")),
        )
    return result


def dotnet_prepare_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(extra)
    for key in ("DOTNET_CLI_HOME", "NUGET_PACKAGES", "NUGET_HTTP_CACHE_PATH"):
        if key in env:
            Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def prepare_adapter(spec: AdapterSpec, workdir: Path) -> None:
    if not spec.prepare:
        return
    env = dotnet_prepare_env(spec.env)
    subprocess.check_call(spec.prepare, cwd=workdir, env=env)


def adapter_available(spec: AdapterSpec) -> bool:
    if not spec.command:
        return False
    first = spec.command[0]
    if first in {"python", "python3", "node", "dotnet"}:
        return True
    return Path(first).exists()


def build_step_command(spec: AdapterSpec, host: str, port: int, step: dict[str, Any]) -> list[str]:
    command = list(spec.command)
    command.extend([host, str(port), step["command"], step.get("address", "")])
    flags: list[str] = []
    for key, value in sorted(step.get("flags", {}).items()):
        flags.extend([f"--{key}", str(value)])
    extra = [str(item) for item in step.get("extra", [])]
    if spec.arg_order == "extra_then_flags":
        command.extend(extra)
        command.extend(flags)
    else:
        command.extend(flags)
        command.extend(extra)
    return command


def run_adapter(spec: AdapterSpec, workdir: Path, host: str, port: int, step: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    env = dotnet_prepare_env(spec.env)
    command = build_step_command(spec, host, port, step)
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timeout", "command": command}
    except Exception as exc:  # pragma: no cover - subprocess environment failure
        return {"status": "error", "message": str(exc), "command": command}

    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        return {
            "status": "error",
            "message": f"exit={completed.returncode} stderr={stderr[:200]}",
            "command": command,
        }
    if not stdout:
        return {"status": "error", "message": "empty stdout", "command": command}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"status": "error", "message": f"invalid json: {exc}", "stdout": stdout, "command": command}
    payload["_command"] = command
    return payload
