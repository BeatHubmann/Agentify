import pytest

from agentify.domain.canonical import canonical_json, content_hash
from agentify.domain.text import normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Approve &amp; send", "Approve & send"),
        ("double &amp;amp; encoded", "double & encoded"),
        ("  padded  ", "padded"),
        ("é", "é"),
        ("keep\nnewline\tand tab", "keep\nnewline\tand tab"),
        ("drop\x00null\x07bell", "dropnullbell"),
        ("&lt;b&gt;bold&lt;/b&gt;", "<b>bold</b>"),
        ("", ""),
    ],
)
def test_normalize_text_examples(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_canonical_json_normalizes_every_string_leaf_and_sorts_keys() -> None:
    payload = {"b": ["x &amp; y", {"inner": " z "}], "a": "café"}
    assert canonical_json(payload) == '{"a":"café","b":["x & y",{"inner":"z"}]}'.encode()


def test_content_hash_is_stable_across_key_order_and_encoding() -> None:
    assert content_hash({"a": 1, "b": "x &amp; y"}) == content_hash({"b": "x & y", "a": 1})
