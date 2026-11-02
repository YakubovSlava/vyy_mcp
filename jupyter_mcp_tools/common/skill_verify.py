"""Verify a skill bundle *before* it is installed, by actually loading it.

The static check in `common/manifest.py` only parses the source. That misses
the failure that matters most: a module which parses fine and then blows up
on import. The motivating case is a code skill doing
`from lib.util import helper` — the archive validator accepts it, and the
agent only finds out when someone calls the tool, with
`ModuleNotFoundError` at call time.

So this module imports the candidate the same way the server will
(`importlib`, unique module name, skill folder *not* on `sys.path`) and
reports the resulting signature. Two deliberate choices:

- **In a subprocess, with a timeout.** Importing a skill executes its
  module-level code. A stray `while True:` or a slow network call at import
  would otherwise hang the MCP server permanently — verification must not be
  able to take the server down. A timeout turns that into an error message.
- **The same interpreter** (`sys.executable`), so a skill importing
  `requests`/`mcp` resolves against exactly the environment the server runs
  in. A validation step that passes in a different environment is worse than
  none.

Verification is best-effort by design: it proves the skill *loads* and has a
usable signature. It never calls the function, because that would run
arbitrary side effects on every install.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 20.0

# Runs in a subprocess. Kept as a string (not a real module) so it can't be
# imported by accident and so the skill's own directory is never on
# sys.path — which is exactly the condition the real loader imposes.
_RUNNER = r'''
import importlib.util, inspect, json, sys, traceback

module_path = sys.argv[1]
result = {"ok": False}

try:
    spec = importlib.util.spec_from_file_location("verify_candidate", module_path)
    if spec is None or spec.loader is None:
        raise ImportError("could not build an import spec for the file")
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_candidate"] = module
    spec.loader.exec_module(module)
except BaseException as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
    result["stage"] = "import"
    print(json.dumps(result))
    raise SystemExit(0)

# The entrypoint is the single function defined in this file (decorators may
# have replaced it with a wrapper, so resolve by module rather than by name).
candidates = [
    obj for name, obj in vars(module).items()
    if callable(obj) and getattr(obj, "__module__", None) == module.__name__
]
if not candidates:
    result["error"] = "the file defines no callable to use as the skill"
    result["stage"] = "introspect"
    print(json.dumps(result))
    raise SystemExit(0)

fn = candidates[0]
try:
    signature = str(inspect.signature(fn))
except (TypeError, ValueError) as exc:
    signature = f"(unavailable: {exc})"

result.update({
    "ok": True,
    "name": getattr(fn, "__name__", ""),
    "signature": signature,
    "doc": (inspect.getdoc(fn) or "").strip(),
    "parameters": [
        {
            "name": p.name,
            "annotation": getattr(p.annotation, "__name__", str(p.annotation))
            if p.annotation is not inspect.Parameter.empty else "",
            "required": p.default is inspect.Parameter.empty,
        }
        for p in inspect.signature(fn).parameters.values()
    ] if signature.startswith("(") else [],
})
print(json.dumps(result))
'''


@dataclass
class VerificationResult:
    ok: bool
    kind: str  # "code" or "doc"
    name: str = ""
    description: str = ""
    details: list[str] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> str:
        """One-paragraph report an agent can act on."""
        if not self.ok:
            return f"Проверка не пройдена ({self.kind}): {self.error}"
        lines = [f"Проверка пройдена ({self.kind}): «{self.name}»"]
        lines.extend(f"- {d}" for d in self.details)
        return "\n".join(lines)


def verify_code_source(source: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> VerificationResult:
    """Import the source in a throwaway process and inspect the entrypoint."""
    if not source.strip():
        return VerificationResult(ok=False, kind="code", error="пустой исходник")

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        module_path = workspace / "candidate.py"
        module_path.write_text(source, encoding="utf-8")

        # The runner lives *outside* the skill's own directory on purpose:
        # Python puts a script's directory on sys.path, which would make a
        # sibling `lib/` importable here and hide exactly the failure this
        # check exists to catch.
        runner_path = workspace / "_runner.py"
        runner_path.write_text(_RUNNER, encoding="utf-8")

        try:
            completed = subprocess.run(
                [sys.executable, str(runner_path), str(module_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(workspace),
            )
        except subprocess.TimeoutExpired:
            return VerificationResult(
                ok=False,
                kind="code",
                error=(
                    f"импорт не завершился за {timeout:g} с — вероятно, код выполняется "
                    "на уровне модуля (бесконечный цикл, сетевой вызов). Вынесите это внутрь функции."
                ),
            )
        except OSError as exc:
            return VerificationResult(ok=False, kind="code", error=f"не удалось запустить проверку: {exc}")

    output = (completed.stdout or "").strip().splitlines()
    payload: dict = {}
    for line in reversed(output):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if not payload:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"код возврата {completed.returncode}"
        return VerificationResult(ok=False, kind="code", error=f"проверка не дала результата: {tail}")

    if not payload.get("ok"):
        stage = payload.get("stage")
        error = payload.get("error") or "неизвестная ошибка"
        if stage == "import":
            hint = ""
            if "No module named" in error:
                hint = (
                    "\nМодуль навыка не видит соседние файлы: папка навыка не добавляется "
                    "в sys.path. Держите код в одном файле; данные рядом читайте через "
                    "__file__." 
                )
            return VerificationResult(ok=False, kind="code", error=error + hint)
        return VerificationResult(ok=False, kind="code", error=error)

    name = payload.get("name") or ""
    doc = payload.get("doc") or ""
    if not doc:
        return VerificationResult(
            ok=False, kind="code", error=f"у функции «{name}» нет docstring — он становится описанием инструмента"
        )

    details = [f"имя: {name}", f"сигнатура: {payload.get('signature', '')}"]
    params = payload.get("parameters") or []
    if params:
        required = [p["name"] for p in params if p.get("required")]
        details.append(f"параметры: {len(params)}" + (f", обязательные: {', '.join(required)}" if required else ", все со значениями по умолчанию"))
    else:
        details.append("параметров нет")
    return VerificationResult(ok=True, kind="code", name=name, description=doc, details=details)


def verify_doc_bundle(body: str, *, name: str, description: str, files: list[str] | None = None) -> VerificationResult:
    """Check a doc skill's fields and that its body isn't a placeholder."""
    problems: list[str] = []
    if not name:
        problems.append("не задано имя")
    if not description.strip():
        problems.append("не задано описание — агент выбирает навык по нему")
    if not body.strip():
        problems.append("пустой текст SKILL.md")
    if problems:
        return VerificationResult(ok=False, kind="doc", error="; ".join(problems))

    details = [f"имя: {name}", f"описание: {len(description)} символов", f"текст: {len(body)} символов, {len(body.splitlines())} строк"]
    if files:
        details.append(f"дополнительные файлы: {len(files)}")
    return VerificationResult(ok=True, kind="doc", name=name, description=description, details=details)
