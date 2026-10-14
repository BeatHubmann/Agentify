"""Text normalization applied to every model- or user-authored string (brief §4.2, HZ-3)."""

import html
import unicodedata

_MAX_PASSES = 16
_KEPT_CONTROLS = frozenset("\n\t")


def _one_pass(text: str) -> str:
    unescaped = html.unescape(text)
    composed = unicodedata.normalize("NFC", unescaped)
    printable = "".join(
        ch for ch in composed if ch in _KEPT_CONTROLS or unicodedata.category(ch) != "Cc"
    )
    return printable.strip()


def normalize_text(text: str) -> str:
    """Decode HTML entities, compose to NFC, drop control characters, trim.

    The steps interact (removing a control character can expose a composable pair, decoding can
    expose leading spaces), so the pipeline is repeated until it reaches a fixpoint. That makes
    the function idempotent by construction.
    """
    current = text
    for _ in range(_MAX_PASSES):
        following = _one_pass(current)
        if following == current:
            return current
        current = following
    raise ValueError("text normalization did not converge")
