"""Skills that ship with the app and are restored automatically.

Two things live here:

1. **A built-in skill list** (`BUILTIN_SKILLS`) — skills that appear in every
   deployment without anyone installing them, and that can't be edited or
   deleted from the admin UI. They're identified purely by name: a folder
   under `tools/<name>/` is "managed" if its name is in this list.

2. **`ensure_builtin_skills()`**, called from the same places that install
   ordinary skills (and from the MCP server's startup), which writes any
   missing built-in skill back. That's what makes them self-healing: a
   delete through the filesystem, a fresh deployment, or someone removing
   the folder by hand all end up with the same set a few seconds later.

Why restore instead of merely hiding: these are part of the app's own
interface (the agent-facing "how do I create a skill" helper), and an agent
that lost them would silently fall back to guessing at the archive format —
exactly the failure the helper exists to prevent.

They are written to disk like any other skill, so the MCP server picks them
up through the ordinary scan — no special-casing in `sync_tools()`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import registry
from .doc_skill import create_doc_skill_dir

# The executor's source is read from inside the package: `examples/` is not
# part of the distribution, so a built-in code skill sourced from there would
# simply be missing after `pip install`.
_CREATE_SKILL_SOURCE = (Path(__file__).resolve().parent.parent / "builtin" / "create_skill.py").read_text(
    encoding="utf-8"
)

logger = logging.getLogger("jupyter_mcp_tools.builtin_skills")


@dataclass(frozen=True)
class BuiltinSkill:
    """A skill that ships with the app and is restored if it goes missing.

    Either kind is supported, because they come in pairs: `skill_builder`
    tells an agent to call `create_skill`, so leaving the executor as an
    ordinary deletable skill would leave the instruction pointing at
    something that can vanish. Both are built in, so the pair can't come
    apart.
    """

    name: str
    description: str
    # Exactly one of these is set, and `kind` follows from which.
    body: str = ""  # doc skill: SKILL.md content
    source: str = ""  # code skill: the .py module, written as tool.py
    group: str = ""

    @property
    def kind(self) -> str:
        return "code" if self.source else "doc"

    def files(self) -> dict[str, str]:
        """The files this skill consists of, keyed by name inside its folder."""
        if self.source:
            return {"tool.py": self.source}
        return {"SKILL.md": self.body}


_SKILL_BUILDING_BODY = """\
# Как создать навык

Ты можешь создавать новые навыки для этого MCP-сервера прямо сейчас, не
обращаясь к администратору. Есть два вида, и выбрать нужно до начала
работы — они устроены по-разному.

## Что вызвать

Вызови инструмент `create_skill`:

| Аргумент | Что писать |
|---|---|
| `kind` | `"doc"` или `"code"` — обязательно |
| `name` | только для `doc`: слаг `[a-z0-9][a-z0-9_-]`, станет именем инструмента |
| `description` | только для `doc`: когда навык использовать, от лица агента |
| `body` | только для `doc`: текст `SKILL.md` в markdown |
| `source` | только для `code`: полный текст одного `.py`-файла |
| `group` | необязательно: имя существующей группы |
| `overwrite` | `true`, чтобы заменить навык с тем же именем |
| `verify_only` | `true`, чтобы только проверить и ничего не записывать |

## Какой вид выбрать

**`doc`** — когда агенту нужно *прочитать* инструкцию, регламент,
справочник, чек-лист. Ничего не исполняется. Сильная сторона — можно
приложить файлы, и агент подгрузит их только при необходимости.

**`code`** — когда при вызове должно *произойти действие*: посчитать,
сходить в API, собрать файлы, обработать данные.

Правило: **должно что-то произойти на сервере → `code`; агент должен
что-то узнать → `doc`.**

## Обязательные требования к `code`

Валидатор строгий, и ошибки возвращаются сразу — вот на чём спотыкаются:

```python
def my_skill(query: str, limit: int = 10) -> str:
    \"\"\"Ищет что-то. Пиши здесь, КОГДА это вызывать — это описание инструмента.\"\"\"
    return f"found {limit} for {query}"
```

- **Ровно одна** функция верхнего уровня. Две → отказ. Вложенные не считаются.
- **Docstring обязателен** — он становится описанием инструмента. Без него установка отклоняется.
- Имя функции = имя навыка, `[a-z0-9][a-z0-9_-]{1,63}`.
- Схема параметров выводится из аннотаций типов — описывать её не нужно.
- Возвращай `str` или JSON-сериализуемое значение.
- Необязательно: `__group__ = "..."` на уровне модуля.

**Ловушка, которая проходит валидацию и падает при вызове:** нельзя
делать `from lib.util import ...` — папка навыка не добавляется в
`sys.path`, и импорт упадёт с `ModuleNotFoundError` уже в момент вызова,
когда чинить будет поздно. Держи весь код в одном файле. Данные рядом
читать можно:

```python
import json
DATA = json.load(open(__file__.replace("tool.py", "data.json")))
```

## Требования к `doc`

- `name` — слаг, станет именем инструмента и папки.
- `description` — пиши «когда использовать», а не «что это».
- `body` — обычный markdown, **без YAML-заголовка**: имя и описание уже
  переданы отдельно.

## Проверка перед установкой

`create_skill` **сам проверяет** навык до записи, так что чинить обычно
ничего не приходится:

- `code`-навык реально импортируется в отдельном процессе — ровно так же,
  как его будет грузить сервер. Поэтому `ModuleNotFoundError` на соседних
  файлах ловится сразу, а не при вызове инструмента.
- Проверяется наличие docstring, корректность имени функции, сигнатура
  параметров.
- Импорт, который зависает (бесконечный цикл или сетевой вызов на уровне
  модуля), обрывается по таймауту с понятным сообщением — сервер от этого
  не ложится.
- `doc`-навык проверяется на непустые имя, описание и текст.

Ответ на успех перечисляет имя, сигнатуру и параметры — по нему видно,
что именно увидят агенты. Сама функция навыка при проверке **не
вызывается**: никаких побочных эффектов при установке.

Сомневаетесь — вызовите с `verify_only=true`: проверка пройдёт, на диск
ничего не попадёт.

## Что произойдёт после вызова

Навык пишется на диск сразу; MCP-сервер пересканирует папку в течение
нескольких секунд, и навык появится в `tools/list`. Перезапуск не нужен.
Если нужен навык с дополнительными файлами (картинки, `references/`) —
это делает администратор через админку, у `create_skill` их нет.
"""

BUILTIN_SKILLS: tuple[BuiltinSkill, ...] = (
    BuiltinSkill(
        name="create_skill",
        description=(
            "Creates a new skill for this MCP server and installs it immediately: "
            "kind=\"doc\" takes name/description/body, kind=\"code\" takes the full .py "
            "source. Use it when the user asks to add or automate something as a skill. "
            "The candidate is verified before it is written, so a rejection means the "
            "skill would not have worked."
        ),
        source=_CREATE_SKILL_SOURCE,
    ),
    BuiltinSkill(
        name="skill_builder",
        description=(
            "Use when you need to create a new skill (tool) for this MCP server, "
            "or when the user asks to add/automate something as a skill. Explains "
            "which of the two kinds to pick, the exact requirements each one must "
            "meet, and how to call create_skill so the new skill is accepted on the "
            "first try instead of bouncing off validation."
        ),
        body=_SKILL_BUILDING_BODY,
    ),
)

BUILTIN_SKILL_NAMES = frozenset(skill.name for skill in BUILTIN_SKILLS)


def is_builtin(name: str) -> bool:
    return name in BUILTIN_SKILL_NAMES


def ensure_builtin_skills(shared_dir: Path) -> list[str]:
    """Write back any built-in skill that is missing. Returns what it wrote.

    Existing folders are left completely alone: an admin who edits one (they
    can't through the UI, but the file is on disk) keeps their edit until
    they delete it, at which point the pristine version comes back. Rewriting
    on every call would also manufacture a `git push` on every sync tick.
    """
    written: list[str] = []
    base = registry.tools_dir(shared_dir)
    base.mkdir(parents=True, exist_ok=True)

    for skill in BUILTIN_SKILLS:
        destination = base / skill.name
        if destination.exists():
            continue
        try:
            _write_builtin(destination, skill)
            written.append(skill.name)
            logger.info("restored built-in skill: %s (%s)", skill.name, skill.kind)
        except Exception:  # noqa: BLE001 - never break the sync loop
            logger.exception("failed to write built-in skill %s", skill.name)
    return written


def _write_builtin(destination: Path, skill: BuiltinSkill) -> None:
    """Materialise one built-in skill on disk.

    Doc skills go through `create_doc_skill_dir` so their `.skill_meta.json`
    sidecar is written exactly the way a normal doc skill's is; a code skill
    is just its module, which is self-describing (name and description are
    the function and its docstring), so no sidecar is needed.
    """
    if skill.kind == "doc":
        create_doc_skill_dir(
            destination, description=skill.description, body=skill.body, group=skill.group
        )
        return

    destination.mkdir(parents=True, exist_ok=True)
    for filename, content in skill.files().items():
        (destination / filename).write_text(content, encoding="utf-8")
