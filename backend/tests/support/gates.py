"""The secret gate as tests construct it: a fixed, non-secret fingerprint key (A§18.3)."""

from agentify.secretgate import SecretGate

FINGERPRINT_KEY = "fingerprint-key-for-integration-tests-" + "f" * 8


def make_gate(*known_values: str) -> SecretGate:
    return SecretGate(known_values, fingerprint_key=FINGERPRINT_KEY)
