"""Thin wrapper around the `curl` binary.

Child-admin instances talk to the main host exclusively through `curl`
subprocess calls (by explicit decision — see PLAN.md) rather than a Python
HTTP client library, so every request a child admin's local relay makes is
visible/auditable the same way a human running `curl` by hand would be.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 30


class CurlError(RuntimeError):
    """curl itself failed to complete the request (network error, timeout, ...)."""


@dataclass
class CurlResponse:
    status: int
    text: str


def _run(cmd: list[str], timeout: int) -> CurlResponse:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CurlError(f"curl timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise CurlError(f"curl exited {result.returncode}: {result.stderr.strip()}")

    body, _, status = result.stdout.rpartition("\n")
    return CurlResponse(status=int(status), text=body)


def _base_cmd(user: str, password: str, insecure: bool) -> list[str]:
    cmd = ["curl", "-s", "-S", "-w", "\n%{http_code}", "-u", f"{user}:{password}"]
    if insecure:
        cmd.append("-k")
    return cmd


def get(
    url: str,
    user: str,
    password: str,
    *,
    insecure: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> CurlResponse:
    cmd = _base_cmd(user, password, insecure) + [url]
    return _run(cmd, timeout)


def download(
    url: str,
    user: str,
    password: str,
    dest_path: Path,
    *,
    insecure: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """GET `url` and write its body straight to `dest_path`, binary-safe —
    unlike `get()`, which captures the body as text and would corrupt a
    zip archive. Used to proxy a skill-archive download through a child
    relay. Returns the HTTP status code; on a non-200 status, `dest_path`
    holds whatever error body the main host sent (typically JSON), which
    the caller can read back for an error message.
    """
    cmd = ["curl", "-s", "-S", "-u", f"{user}:{password}"]
    if insecure:
        cmd.append("-k")
    cmd += ["-o", str(dest_path), "-w", "%{http_code}", url]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CurlError(f"curl timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise CurlError(f"curl exited {result.returncode}: {result.stderr.strip()}")

    return int(result.stdout.strip())


def post(
    url: str,
    user: str,
    password: str,
    *,
    fields: dict[str, str] | None = None,
    files: dict[str, str] | None = None,
    insecure: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> CurlResponse:
    """POST as multipart/form-data.

    `fields` are plain form values; `files` maps a form field name to a
    local filesystem path whose contents should be uploaded under that
    field (curl's `-F name=@path`).
    """
    cmd = _base_cmd(user, password, insecure) + ["-X", "POST"]
    for key, value in (fields or {}).items():
        cmd += ["-F", f"{key}={value}"]
    for key, path in (files or {}).items():
        cmd += ["-F", f"{key}=@{path}"]
    cmd.append(url)
    return _run(cmd, timeout)
