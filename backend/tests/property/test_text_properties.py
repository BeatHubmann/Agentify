from hypothesis import given
from hypothesis import strategies as st

from agentify.domain.canonical import canonical_json
from agentify.domain.text import normalize_text

FRAGMENTS = st.sampled_from([*"ab &;#<>\n\t\x00e", "́", "&amp;", "&lt;", "&#39;", "&#32;", "&#7;"])
ENTITY_HEAVY = st.lists(FRAGMENTS, max_size=24).map("".join) | st.text()
JSON_TREES = st.recursive(
    st.none() | st.booleans() | st.integers() | ENTITY_HEAVY,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=5), children, max_size=4)
    ),
    max_leaves=12,
)


@given(ENTITY_HEAVY)
def test_normalize_text_is_idempotent(raw: str) -> None:
    once = normalize_text(raw)
    assert normalize_text(once) == once


@given(JSON_TREES)
def test_canonical_json_is_a_fixpoint_of_its_own_output(tree: object) -> None:
    import json

    encoded = canonical_json(tree)
    assert canonical_json(json.loads(encoded)) == encoded
