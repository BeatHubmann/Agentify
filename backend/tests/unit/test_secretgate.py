import pytest

from agentify.secretgate import Scrubbed, SecretFound, SecretGate

KNOWN = "s3cr3t-value-for-tests"
# Built at runtime so the repository holds no credential-shaped literal (secret scanners, push protection).
FAKE_AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"


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
        SecretGate([KNOWN]).check_bytes(content.encode(), "test")
    assert (hit.value.rule, KNOWN in str(hit.value)) == (rule, False)


def test_gate_passes_ordinary_process_text() -> None:
    text = b"Receive the invoice, classify it, compute the fee and approve it."
    assert SecretGate([KNOWN]).check_bytes(text, "test").value == text


def test_scrubbed_cannot_be_forged_outside_the_gate() -> None:
    with pytest.raises(TypeError):
        Scrubbed(b"x", object())


def test_redaction_keeps_text_and_removes_known_values_and_patterns() -> None:
    gate = SecretGate([KNOWN])
    text = f"user said {KNOWN} and pasted {FAKE_AWS_KEY_ID} into the chat"
    assert gate.redact_text(text).value == (
        "user said [REDACTED:known-value] and pasted [REDACTED:aws-access-key-id] into the chat"
    )
