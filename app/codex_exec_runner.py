from __future__ import annotations

import os
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class CodexExecConfig:
    enabled: bool = True
    provider: str = "codex"
    command: str = "codex"
    cwd: str = ""
    timeout_seconds: int = 3600
    model: str = ""
    effort: str = ""
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodexExecResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    text: str = ""
    session_id: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def config_from_mapping(values: dict[str, Any] | None) -> CodexExecConfig:
    values = values or {}
    return CodexExecConfig(
        enabled=bool(values.get("codex_exec_enabled", True)),
        provider=str(values.get("codex_exec_provider") or "codex"),
        command=str(values.get("codex_exec_command") or "codex"),
        cwd=str(values.get("codex_exec_cwd") or ""),
        timeout_seconds=int(values.get("codex_exec_timeout_seconds") or 3600),
        model=str(values.get("codex_exec_model") or ""),
        effort=str(values.get("codex_exec_effort") or ""),
        extra_args=tuple(str(arg) for arg in values.get("codex_exec_extra_args", []) if str(arg).strip()),
    )


def run_codex_exec(
    prompt: str,
    *,
    config: CodexExecConfig | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: int | None = None,
    env: dict[str, str] | None = None,
    session_id: str = "",
) -> CodexExecResult:
    config = config or CodexExecConfig()
    if not config.enabled:
        raise RuntimeError("AI CLI execution is disabled by config")

    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("AI CLI prompt is empty")

    workdir = Path(cwd or config.cwd or Path.cwd())
    command, stdin_text, cleanup_paths, output_path = build_ai_cli_command(
        config, workdir, prompt, session_id=session_id
    )
    merged_env = os.environ.copy()
    merged_env.setdefault("PYTHONUTF8", "1")
    merged_env.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        merged_env.update(env)

    completed = subprocess.run(
        command,
        cwd=str(workdir),
        env=merged_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
            timeout=timeout_seconds or config.timeout_seconds,
            input=stdin_text,
    )
    event_stdout = completed.stdout
    stdout = event_stdout
    if output_path and output_path.exists():
        stdout = output_path.read_text(encoding="utf-8", errors="replace")
    text = parse_ai_cli_text(config.provider, stdout, completed.stderr)
    resolved_session_id = parse_codex_session_id(event_stdout) if normalize_provider(config.provider) == "codex" else ""
    cleanup_temp_paths(cleanup_paths)
    return CodexExecResult(
        command=command,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=completed.stderr,
        text=text,
        session_id=resolved_session_id or str(session_id or ""),
    )


def command_path(name: str) -> str:
    candidates = {
        "grok": Path.home() / ".grok" / "bin" / "grok.exe",
        "codex": Path.home() / "AppData" / "Roaming" / "npm" / "codex.cmd",
        "claude": Path.home() / ".local" / "bin" / "claude.exe",
    }
    local = candidates.get(name)
    if local and local.is_file():
        return str(local)
    return shutil.which(name) or name


def build_ai_cli_command(
    config: CodexExecConfig,
    workdir: Path,
    prompt: str,
    *,
    session_id: str = "",
) -> tuple[list[str], str | None, list[Path], Path | None]:
    provider = normalize_provider(config.provider)
    cleanup: list[Path] = []
    if provider == "claude":
        command = [
            command_path(config.command if config.command != "codex" else "claude"),
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if config.model:
            command.extend(["--model", config.model])
        if config.effort:
            command.extend(["--effort", config.effort])
        command.extend(config.extra_args)
        return command, prompt, cleanup, None
    if provider == "grok":
        prompt_file = temp_file(workdir, "grok_prompt")
        prompt_file.write_text(prompt, encoding="utf-8", newline="\n")
        cleanup.append(prompt_file)
        command = [
            command_path(config.command if config.command != "codex" else "grok"),
            "--no-auto-update",
            "--no-alt-screen",
            "--cwd",
            str(workdir),
            "--sandbox",
            "workspace",
            "--tools",
            "read_file,list_dir,grep",
            "--output-format",
            "json",
        ]
        if config.model:
            command.extend(["-m", config.model])
        if config.effort:
            command.extend(["--effort", config.effort])
        command.extend(config.extra_args)
        command.extend(["--prompt-file", str(prompt_file)])
        return command, None, cleanup, None

    output_path = temp_file(workdir, "codex_reply")
    cleanup.append(output_path)
    executable = command_path(config.command if config.command != "codex" else "codex")
    if session_id:
        command = [
            executable, "exec", "resume", "--json",
            "--output-last-message", str(output_path),
        ]
    else:
        command = [
            executable, "exec", "--json", "--cd", str(workdir),
            "--sandbox", "danger-full-access",
            "--output-last-message", str(output_path),
        ]
    if config.model:
        command.extend(["--model", config.model])
    if config.effort:
        command.extend(["--config", f'model_reasoning_effort="{config.effort}"'])
    command.extend(config.extra_args)
    if session_id:
        command.append(str(session_id))
    command.append("-")
    return command, prompt, cleanup, output_path


def parse_codex_session_id(stdout: str) -> str:
    for line in str(stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") in {"thread.started", "thread.created"}:
            value = event.get("thread_id") or event.get("session_id") or event.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def normalize_provider(provider: str) -> str:
    value = str(provider or "codex").strip().lower()
    if value in {"claudecode", "claude-code"}:
        return "claude"
    if value in {"grok_build", "grok-build"}:
        return "grok"
    if value in {"codex_exec", "codex-exec"}:
        return "codex"
    return value if value in {"codex", "claude", "grok"} else "codex"


def parse_ai_cli_text(provider: str, stdout: str, stderr: str) -> str:
    provider = normalize_provider(provider)
    if provider in {"claude", "grok"}:
        try:
            data = json.loads(str(stdout or "").strip())
        except json.JSONDecodeError:
            return (stdout or stderr or "").strip()
        for key in ("result", "response", "text", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        content = data.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts).strip()
        return (stderr or stdout or "").strip()
    return (stdout or stderr or "").strip()


def extract_reply_json_value(text: str) -> str:
    """Extract only a string `reply` value from any JSON object in AI output."""
    source = str(text or "")
    decoder = json.JSONDecoder()
    for index, character in enumerate(source):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(source[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        reply = value.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()
    return ""


def temp_file(workdir: Path, prefix: str) -> Path:
    prompt_dir = workdir / ".niconico_watch_ai_cli"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    return prompt_dir / f"{prefix}_{uuid4().hex}.txt"


def cleanup_temp_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
