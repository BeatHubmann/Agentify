"""Specification artifacts (A§6.11). A paragraph's sources quote the inputs they come from; the
quote is checked against the input by code, so a citation cannot be invented."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentify.domain.text import normalize_text

SectionName = Literal["current", "desired", "constraints"]
SECTION_ORDER: tuple[SectionName, ...] = ("current", "desired", "constraints")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputCitation(_Model):
    input: str = Field(pattern=r"^inputs/[A-Za-z0-9._-]+$")
    quote: str = Field(min_length=3, max_length=2_000)

    _norm = field_validator("quote")(normalize_text)


class SpecParagraph(_Model):
    id: str = Field(pattern=r"^[a-z]+-[0-9]{3}$")
    text: str = Field(min_length=1, max_length=20_000)
    sources: tuple[InputCitation, ...] = ()

    _norm = field_validator("text")(normalize_text)


class SpecSection(_Model):
    section: SectionName
    paragraphs: tuple[SpecParagraph, ...] = ()


class Specification(_Model):
    sections: tuple[SpecSection, ...] = ()
