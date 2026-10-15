"""Secret-gate false positives (INV-8.ALLOW, A§10.2, A§18.3): a deployment admin allow-lists one
exact fingerprint in one engagement, with a justification; known credential values never pass."""

import hashlib
import hmac
from typing import Any

import httpx
import pytest

from tests.api.conftest import ADMIN_SUBJECT, TokenFactory, engagement_with, sign_in, subject
from tests.conftest import KNOWN_SECRET
from tests.support.gates import FINGERPRINT_KEY

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
# AWS's documented example access key, built at runtime so no credential-shaped literal is committed.
EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
OTHER_KEY = "AKIA" + "ZXCVBNMASDFGHJKL"
JUSTIFICATION = "AWS's documented example key, quoted in the client's integration spec"


def keyed(value: str) -> str:
    return hmac.new(FINGERPRINT_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


async def consultant_process(api: httpx.AsyncClient, token: TokenFactory) -> dict[str, Any]:
    who = subject()
    setup = await engagement_with(api, token, {who: "consultant"})
    headers = setup["users"][who][1]
    created = await api.post(
        f"/engagements/{setup['engagement_id']}/processes", json={"name": "p"}, headers=headers
    )
    return {**setup, "process_id": created.json()["process_id"], "headers": headers}


async def upload(
    api: httpx.AsyncClient, where: dict[str, Any], name: str, key: str = EXAMPLE_KEY
) -> int:
    response = await api.post(
        f"/processes/{where['process_id']}/inputs",
        json={"name": name, "text": f"Sign requests with {key}."},
        headers=where["headers"],
    )
    return response.status_code


async def test_an_allow_listed_fingerprint_passes_only_in_its_engagement_until_revoked(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    a, b = await consultant_process(api, token), await consultant_process(api, token)
    admin_id, admin = await sign_in(api, token, ADMIN_SUBJECT)
    engagement = a["engagement_id"]
    before = await upload(api, a, "spec-1.md")
    events = (await api.get(f"/engagements/{engagement}/security-events", headers=admin)).json()
    entry = {
        "rule": events[0]["rule"],
        "fingerprint": events[0]["fingerprint"],
        "action": "allow",
        "justification": JUSTIFICATION,
    }
    added = await api.post(f"/engagements/{engagement}/secret-allowlist", json=entry, headers=admin)
    in_a, in_b = await upload(api, a, "spec-2.md"), await upload(api, b, "spec-1.md")
    other_key_in_a = await upload(api, a, "spec-other.md", key=OTHER_KEY)
    revoked = await api.post(
        f"/engagements/{engagement}/secret-allowlist",
        json={**entry, "action": "revoke", "justification": "the client removed the example"},
        headers=admin,
    )
    after_revoke = await upload(api, a, "spec-3.md")
    listed = (await api.get(f"/engagements/{engagement}/secret-allowlist", headers=admin)).json()
    assert (
        before,
        [(e["exit"], e["rule"], e["fingerprint"]) for e in events],
        added.status_code,
        (in_a, in_b, other_key_in_a),
        revoked.status_code,
        after_revoke,
        [(e["action"], e["admin_id"], e["justification"]) for e in listed],
    ) == (
        422,
        [("blob", "aws-access-key-id", keyed(EXAMPLE_KEY))],
        201,
        (201, 422, 422),
        201,
        422,
        [
            ("allow", admin_id, JUSTIFICATION),
            ("revoke", admin_id, "the client removed the example"),
        ],
    )


async def test_known_values_and_non_admins_cannot_allow_list(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    a = await consultant_process(api, token)
    _admin_id, admin = await sign_in(api, token, ADMIN_SUBJECT)
    url = f"/engagements/{a['engagement_id']}/secret-allowlist"
    base = {"action": "allow", "justification": JUSTIFICATION}
    as_known = {**base, "rule": "known-value", "fingerprint": keyed(KNOWN_SECRET)}
    known_under_a_pattern = {
        **base,
        "rule": "aws-access-key-id",
        "fingerprint": keyed(KNOWN_SECRET),
    }
    example = {**base, "rule": "aws-access-key-id", "fingerprint": keyed(EXAMPLE_KEY)}
    leaky = {**example, "justification": f"the key is {KNOWN_SECRET}"}
    responses = [
        await api.post(url, json=as_known, headers=admin),
        await api.post(url, json=known_under_a_pattern, headers=admin),
        await api.post(url, json=example, headers=a["headers"]),
        await api.post(url, json=leaky, headers=admin),
        await api.get(f"/engagements/{a['engagement_id']}/security-events", headers=a["headers"]),
    ]
    listed = (await api.get(url, headers=admin)).json()
    events = await api.get(f"/engagements/{a['engagement_id']}/security-events", headers=admin)
    assert (
        [(r.status_code, r.json()["code"]) for r in responses],
        any(KNOWN_SECRET in r.text for r in [*responses, events]),
        listed,
        [(e["exit"], e["rule"], e["fingerprint"]) for e in events.json()],
    ) == (
        [
            (422, "known_value_not_allowable"),
            (422, "known_value_not_allowable"),
            (403, "forbidden"),
            (422, "secret_detected"),
            (403, "forbidden"),
        ],
        False,
        [],
        [("decision", "known-value", keyed(KNOWN_SECRET))],
    )
