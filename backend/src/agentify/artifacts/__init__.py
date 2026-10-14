"""Registry artifact types: built-in schemas and the declarative check library (A§9.4, A§8.6).

An artifact type declares which checks run on every write (correctable errors to the agent) and
which run in the harness gate and at commit (completeness). A new type is a declaration; a new
*kind* of check is one function here plus its test (deviation D-15)."""

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agentify.domain.spec import SECTION_ORDER, Specification, SpecSection
from agentify.domain.text import normalize_text
from agentify.domain.validate import Violation

BUILTIN_SCHEMAS: dict[str, type[BaseModel]] = {
    "builtin:spec_section": SpecSection,
    "builtin:specification": Specification,
}

InputReader = Callable[[str], Awaitable[bytes | None]]


@dataclass(frozen=True)
class CheckContext:
    read_input: InputReader


Check = Callable[[BaseModel, CheckContext], Awaitable[list[Violation]]]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(text)).strip()


async def _sources_grounded(body: BaseModel, ctx: CheckContext) -> list[Violation]:
    found: list[Violation] = []
    cache: dict[str, str | None] = {}
    for paragraph in _paragraphs(body):
        for source in paragraph.sources:
            if source.input not in cache:
                data = await ctx.read_input(source.input)
                cache[source.input] = (
                    None if data is None else data.decode("utf-8", errors="replace")
                )
            text = cache[source.input]
            if text is None:
                found.append(
                    Violation("unknown_input", f"{source.input} does not exist.", (paragraph.id,))
                )
            elif _squash(source.quote) not in _squash(text):
                found.append(
                    Violation(
                        "quote_not_in_input",
                        f"The quote is not found verbatim in {source.input}.",
                        (paragraph.id,),
                    )
                )
    return found


async def _paragraph_ids_unique(body: BaseModel, _ctx: CheckContext) -> list[Violation]:
    ids = [p.id for p in _paragraphs(body)]
    return [
        Violation("duplicate_paragraph_id", f"{i} is used twice.", (i,))
        for i in sorted(set(ids))
        if ids.count(i) > 1
    ]


async def _section_complete(body: BaseModel, _ctx: CheckContext) -> list[Violation]:
    if not isinstance(body, SpecSection):
        raise TypeError("spec_section.complete applies to spec sections only")
    if not body.paragraphs:
        return [Violation("section_empty", f"The {body.section} section has no paragraphs.")]
    return [
        Violation("paragraph_unsourced", "Every paragraph needs at least one source.", (p.id,))
        for p in body.paragraphs
        if not p.sources
    ]


async def _specification_complete(body: BaseModel, _ctx: CheckContext) -> list[Violation]:
    if not isinstance(body, Specification):
        raise TypeError("specification.complete applies to specifications only")
    names = tuple(s.section for s in body.sections)
    if names != SECTION_ORDER:
        return [
            Violation("sections_out_of_order", f"Sections must be {SECTION_ORDER}, found {names}.")
        ]
    return [
        Violation("section_empty", f"The {s.section} section is empty.")
        for s in body.sections
        if not s.paragraphs
    ]


def _paragraphs(body: BaseModel) -> list[Any]:
    if isinstance(body, SpecSection):
        return list(body.paragraphs)
    if isinstance(body, Specification):
        return [p for s in body.sections for p in s.paragraphs]
    return []


CHECKS: dict[str, Check] = {
    "spec.sources_grounded": _sources_grounded,
    "spec.paragraph_ids_unique": _paragraph_ids_unique,
    "spec_section.complete": _section_complete,
    "specification.complete": _specification_complete,
}


def parse_body(schema_ref: str, body: Mapping[str, Any]) -> BaseModel | list[Violation]:
    model = BUILTIN_SCHEMAS.get(schema_ref)
    if model is None:
        return [Violation("unknown_schema", f"{schema_ref} is not a known schema.")]
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return [Violation("schema_violation", f"{where}: {first['msg']}", (where,))]


async def run_checks(names: tuple[str, ...], body: BaseModel, ctx: CheckContext) -> list[Violation]:
    found: list[Violation] = []
    for name in names:
        found.extend(await CHECKS[name](body, ctx))
    return found


def check_names() -> frozenset[str]:
    return frozenset(CHECKS)


def as_json(body: BaseModel) -> str:
    return json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
