"""Whatever text surrounds a known value, no gated output carries it (A§18.1)."""

import base64
import urllib.parse

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentify.secretgate import SecretFound, SecretGate

KNOWN = "known/value+for=property tests"
GATE = SecretGate([KNOWN], fingerprint_key="fingerprint-key-for-property-tests-" + "p" * 8)
FORMS = [KNOWN, base64.b64encode(KNOWN.encode()).decode(), urllib.parse.quote(KNOWN, safe="")]
# Includes lone surrogates, which JSON decoding produces from model output ("\\ud800").
ANY_TEXT = st.lists(st.characters() | st.sampled_from(["\ud800", "\udfff"])).map("".join)


@given(before=ANY_TEXT, after=ANY_TEXT, form=st.sampled_from(FORMS))
def test_redaction_never_leaves_a_known_value_in_any_form(
    before: str, after: str, form: str
) -> None:
    redacted = GATE.redact_text(before + form + after).value
    assert [f for f in FORMS if f in redacted] == []


@given(before=ANY_TEXT, after=ANY_TEXT, form=st.sampled_from(FORMS))
def test_a_write_carrying_a_known_value_in_any_form_is_refused(
    before: str, after: str, form: str
) -> None:
    with pytest.raises(SecretFound):
        GATE.check_bytes((before + form + after).encode("utf-8", "surrogatepass"), "property")
