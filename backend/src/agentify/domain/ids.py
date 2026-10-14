"""Distinct identifier types (C-5). Agent and user items live in separate namespaces (A§6.2)."""

import re
from typing import Annotated, NewType

from pydantic import StringConstraints

AGENT_ID_PATTERN = r"^(?!u_)[a-z][a-z0-9_-]{0,63}$"
USER_ID_PATTERN = r"^u_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ITEM_ID_SHAPE = r"^[a-z][a-z0-9_-]{0,63}$"

ItemId = Annotated[str, StringConstraints(pattern=ITEM_ID_SHAPE)]
"""A node or edge id in either namespace. The schema checks only the shape; which namespace an id
belongs to is checked by `is_agent_id` / `is_user_id` (integrity, A§6.1), because the schema's
regex engine has no look-ahead."""

ProcessId = NewType("ProcessId", str)
RunId = NewType("RunId", str)
RevisionId = NewType("RevisionId", str)
ContentHash = NewType("ContentHash", str)
UserId = NewType("UserId", str)

_AGENT_ID = re.compile(AGENT_ID_PATTERN)
_USER_ID = re.compile(USER_ID_PATTERN)


def is_agent_id(item_id: str) -> bool:
    return _AGENT_ID.fullmatch(item_id) is not None


def is_user_id(item_id: str) -> bool:
    return _USER_ID.fullmatch(item_id) is not None
