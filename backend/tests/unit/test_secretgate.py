import asyncio
import hashlib
import hmac

import pytest

from agentify.secretgate import Hit, Scrubbed, SecretFound, SecretGate

KNOWN = "s3cr3t-value-for-tests"
FINGERPRINT_KEY = "fingerprint-key-for-unit-tests-" + "k" * 16
# Built at runtime so the repository holds no credential-shaped literal (secret scanners, push protection).
FAKE_AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
AWS_EXAMPLE_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"


def gate(*known: str) -> SecretGate:
    return SecretGate(known, fingerprint_key=FINGERPRINT_KEY)


def keyed(value: str, key: str = FINGERPRINT_KEY) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    ("content", "rule"),
    [
        (f"prefix {KNOWN} suffix", "known-value"),
        ("czNjcjN0LXZhbHVlLWZvci10ZXN0cw==", "known-value"),
        (f"key {FAKE_AWS_KEY_ID} here", "aws-access-key-id"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key-block"),
        ("token ghp_" + "a" * 36, "github-token"),
    ],
    ids=["known-literal", "known-base64", "aws-key-id", "private-key", "github-token"],
)
def test_gate_refuses_known_values_and_credential_patterns(content: str, rule: str) -> None:
    with pytest.raises(SecretFound) as hit:
        gate(KNOWN).check_bytes(content.encode(), "test")
    assert (hit.value.rule, KNOWN in str(hit.value)) == (rule, False)


def test_gate_passes_ordinary_process_text() -> None:
    text = b"Receive the invoice, classify it, compute the fee and approve it."
    assert gate(KNOWN).check_bytes(text, "test").value == text


def test_scrubbed_cannot_be_forged_outside_the_gate() -> None:
    with pytest.raises(TypeError):
        Scrubbed(b"x", object())


def test_redaction_keeps_text_and_removes_known_values_and_patterns() -> None:
    text = f"user said {KNOWN} and pasted {FAKE_AWS_KEY_ID} into the chat"
    assert gate(KNOWN).redact_text(text).value == (
        "user said [REDACTED:known-value] and pasted [REDACTED:aws-access-key-id] into the chat"
    )


def test_redaction_reports_each_hit_by_rule_and_fingerprint_only() -> None:
    redacted = gate(KNOWN).redact_text(f"{KNOWN} then {FAKE_AWS_KEY_ID}")
    assert redacted.hits == (
        Hit("known-value", keyed(KNOWN)),
        Hit("aws-access-key-id", keyed(FAKE_AWS_KEY_ID)),
    )


def refused_fingerprint(checked: SecretGate, content: str) -> str:
    with pytest.raises(SecretFound) as hit:
        checked.check_bytes(content.encode(), "test")
    return hit.value.fingerprint


def test_a_known_value_has_one_fingerprint_in_every_encoding() -> None:
    forms = [KNOWN, "czNjcjN0LXZhbHVlLWZvci10ZXN0cw=="]  # literal and base64
    assert [refused_fingerprint(gate(KNOWN), form) for form in forms] == [keyed(KNOWN)] * 2


def test_fingerprints_change_with_the_deployment_key() -> None:
    other_key = "another-fingerprint-key-for-tests-" + "o" * 16
    other = SecretGate(fingerprint_key=other_key).redact_text(FAKE_AWS_KEY_ID).hits
    same = gate().redact_text(FAKE_AWS_KEY_ID).hits
    assert (same[0].fingerprint, other[0].fingerprint) == (
        keyed(FAKE_AWS_KEY_ID),
        keyed(FAKE_AWS_KEY_ID, other_key),
    )


def test_the_fingerprint_key_is_itself_a_known_value() -> None:
    assert gate().redact_text(f"key={FINGERPRINT_KEY}").value == "key=[REDACTED:known-value]"


def test_a_short_fingerprint_key_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        SecretGate(fingerprint_key="short")


def test_an_allowed_fingerprint_passes_only_its_exact_value() -> None:
    allowed = frozenset({Hit("aws-access-key-id", keyed(AWS_EXAMPLE_KEY_ID))})
    passed = gate().check_bytes(f"example {AWS_EXAMPLE_KEY_ID}".encode(), "test", allowed=allowed)
    with pytest.raises(SecretFound) as other:
        gate().check_bytes(f"real {FAKE_AWS_KEY_ID}".encode(), "test", allowed=allowed)
    assert (passed.value, other.value.fingerprint) == (
        f"example {AWS_EXAMPLE_KEY_ID}".encode(),
        keyed(FAKE_AWS_KEY_ID),
    )


def test_a_known_value_cannot_be_allowed_even_when_it_matches_a_pattern() -> None:
    configured = gate(FAKE_AWS_KEY_ID)
    allowed = frozenset(
        {
            Hit("aws-access-key-id", keyed(FAKE_AWS_KEY_ID)),
            Hit("known-value", keyed(FAKE_AWS_KEY_ID)),
        }
    )
    with pytest.raises(SecretFound) as hit:
        configured.check_bytes(FAKE_AWS_KEY_ID.encode(), "test", allowed=allowed)
    assert (hit.value.rule, configured.is_known_fingerprint(keyed(FAKE_AWS_KEY_ID))) == (
        "known-value",
        True,
    )


def test_registered_values_are_matched_while_any_holder_lives() -> None:
    shared = gate()
    seen = []
    with shared.registered([KNOWN]):
        with shared.registered([KNOWN]):
            seen.append(shared.redact_text(KNOWN).value)
        seen.append(shared.redact_text(KNOWN).value)
    seen.append(shared.redact_text(KNOWN).value)
    assert seen == ["[REDACTED:known-value]", "[REDACTED:known-value]", KNOWN]


@pytest.mark.anyio
async def test_concurrent_contexts_match_each_others_values() -> None:
    """Every sink sees every live context's values: run A's sink redacts run B's credential (A§17.2)."""
    shared = gate()
    first, second = "credential-of-run-a-0001", "credential-of-run-b-0002"
    both_registered = asyncio.Barrier(2)

    async def run(own: str, other: str) -> str:
        with shared.registered([own]):
            await both_registered.wait()
            text = shared.redact_text(f"echo {other}").value
            await both_registered.wait()
            return text

    results = await asyncio.gather(run(first, second), run(second, first))
    assert results == ["echo [REDACTED:known-value]", "echo [REDACTED:known-value]"]


def test_known_hits_report_registered_values_in_every_encoding_and_ignore_patterns() -> None:
    """Model requests (A§18.2): a known value refuses the call; client text that merely looks like
    a credential is the input and passes."""
    import base64

    known = "model-request-known-credential"
    holder = gate(known)
    found = [
        holder.known_hits(text.encode())
        for text in (
            f"tool result: {known}",
            f"encoded: {base64.b64encode(known.encode()).decode()}",
            f"the spec quotes {AWS_EXAMPLE_KEY_ID}",
            "ordinary process text",
        )
    ]
    assert [[h.rule for h in hits] for hits in found] == [["known-value"], ["known-value"], [], []]


def test_a_known_value_with_json_escaped_characters_is_found_in_serialized_json() -> None:
    """Model requests and run events are JSON: a quote or backslash in the value is escaped there."""
    import json

    value = 'pa"ss\\word-credential'
    holder = gate(value)
    serialized = json.dumps({"content": f"tool result: {value}"}).encode()
    assert (
        [h.rule for h in holder.known_hits(serialized)],
        value in holder.redact_text(serialized.decode()).value,
        holder.redact_text(serialized.decode()).value.count("[REDACTED:known-value]"),
    ) == (["known-value"], False, 1)
