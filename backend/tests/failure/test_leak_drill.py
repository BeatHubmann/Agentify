"""Leak drill per exit (INV-8, A§18): plant a credential, then search each sink for it from outside
the gate. Every hit must also leave a security event that holds no secret (A§18.3)."""

import base64
import hashlib
import hmac
import urllib.parse
import uuid
from typing import Any

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.secretgate import Hit
from agentify.store import security_events as security
from agentify.store.workspace import Workspace
from tests.conftest import BUCKET, KNOWN_SECRET, Infra, s3_client
from tests.orchestration.test_run_exit_paths import FLOW_PATH, history, leftovers, run_once
from tests.support.agents import add_process_input, run_kind, section_steps
from tests.support.gates import FINGERPRINT_KEY
from tests.support.scripted import Script, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
# Built at runtime so the repository holds no credential-shaped literal.
PATTERN_ONLY = "AKIA" + "QWERTYUIOPASDFGH"
CANARIES = pytest.mark.parametrize(
    ("canary", "rule"),
    [(KNOWN_SECRET, "known-value"), (PATTERN_ONLY, "aws-access-key-id")],
    ids=["known-value", "pattern-only"],
)


def forms(canary: str) -> list[bytes]:
    raw = canary.encode()
    return [raw, base64.b64encode(raw), urllib.parse.quote(canary, safe="").encode()]


def fingerprint(canary: str) -> str:
    return hmac.new(FINGERPRINT_KEY.encode(), canary.encode(), hashlib.sha256).hexdigest()


def stored_objects_holding(infra: Infra, engagement_id: str, canary: str) -> list[str]:
    """Every object under the engagement, committed or not, searched byte by byte."""
    client = s3_client(infra)
    listed = client.list_objects_v2(Bucket=BUCKET, Prefix=f"{engagement_id}/")
    found = []
    for obj in listed.get("Contents", []):
        body = client.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
        if any(form in body for form in forms(canary)):
            found.append(obj["Key"])
    return found


async def engagement_of(ws: Workspace, process_id: str) -> str:
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute("SELECT engagement_id FROM process WHERE id = %s", (process_id,))
        ).fetchone()
    assert row is not None
    return str(row["engagement_id"])


async def security_events(ws: Workspace, run_id: str) -> list[dict[str, Any]]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT exit, rule, fingerprint, location FROM security_event
                   WHERE run_id = %s ORDER BY seq""",
                (run_id,),
            )
        ).fetchall()
    return [dict(row) for row in rows]


def leaking_node(canary: str) -> Step:
    label = f"receive the invoice, key {canary}"
    return Step(
        calls=[
            ("add_node", {"id": "receive", "label": label, "kind": "step", "execution": "external"})
        ]
    )


@CANARIES
async def test_a_staged_write_carrying_a_credential_is_correctable_once(
    *,
    infra: Infra,
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    canary: str,
    rule: str,
) -> None:
    script = Script([leaking_node(canary), *golden_calls()])
    run_id, final = await run_once(workspace, registry, process_id, script)
    events = await security_events(workspace, run_id)
    retry = str(script.seen[1][-1])
    assert (
        final,
        stored_objects_holding(infra, await engagement_of(workspace, process_id), canary),
        events,
        "secret_in_content" in retry,
        any(form.decode() in retry for form in forms(canary)),
    ) == (
        "committed",
        [],
        [
            {
                "exit": "staged_write",
                "rule": rule,
                "fingerprint": fingerprint(canary),
                "location": FLOW_PATH,
            }
        ],
        True,
        False,
    )


@CANARIES
async def test_repeating_the_same_credential_aborts_the_run(
    *,
    infra: Infra,
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    canary: str,
    rule: str,
) -> None:
    script = Script([leaking_node(canary), leaking_node(canary), *golden_calls()])
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    events = await security_events(workspace, run_id)
    assert (
        final,
        state["outcome"],
        str(state["outcome_detail_scrubbed"]).split(":")[0],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
        stored_objects_holding(infra, await engagement_of(workspace, process_id), canary),
        [(e["rule"], e["fingerprint"]) for e in events],
    ) == (
        "rolled_back",
        "aborted",
        "secret_gate_repeated",
        [],
        (0, 0),
        [],
        [(rule, fingerprint(canary))] * 2,
    )


def holds(text: str, canary: str) -> bool:
    return any(form.decode() in text for form in forms(canary))


@CANARIES
async def test_an_unexpected_exception_reaches_the_run_record_scrubbed(
    *, workspace: Workspace, registry: Registry, process_id: str, canary: str, rule: str
) -> None:
    class BrokenRead(Workspace):
        async def read_staged_or_base(self, *args: Any, **kwargs: Any) -> bytes | None:
            raise KeyError(f"bug near {canary}")

    ws = BrokenRead(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(
        ws, registry, process_id, Script([Step(calls=[("read_flow", {})])])
    )
    state = await runs.run_state(ws, run_id)
    detail = str(state["outcome_detail_scrubbed"])
    events = await security_events(ws, run_id)
    assert (
        final,
        state["outcome"],
        detail.splitlines()[0],
        f"KeyError: 'bug near [REDACTED:{rule}]'" in detail,
        holds(detail, canary),
        [(e["exit"], e["rule"], e["fingerprint"]) for e in events],
    ) == (
        "rolled_back",
        "aborted",
        "KeyError",
        True,
        False,
        [("run_record", rule, fingerprint(canary))],
    )


async def test_a_gate_report_quoting_an_allow_listed_value_is_stored_redacted(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """Allow-listing lets a value into an artifact; redacting exits such as run records still redact it."""
    sample = "xoxb-" + "1234567890-sample"  # a pattern-only token shape that a node id may take
    engagement_id = await engagement_of(workspace, process_id)
    admin_id = f"usr_{uuid.uuid4().hex}"
    async with workspace.db.tx() as conn:
        await conn.execute(
            "INSERT INTO app_user (id, issuer, subject, is_admin) VALUES (%s, 'test', %s, true)",
            (admin_id, admin_id),
        )
    await security.add_allowlist_entry(
        workspace.db,
        engagement_id=engagement_id,
        hit=Hit("slack-token", fingerprint(sample)),
        action="allow",
        justification="sample token quoted in the client specification",
        admin_id=admin_id,
    )
    container = {"id": sample, "label": "Coordinator", "kind": "orchestrator", "execution": "ai"}
    script = Script(
        [*golden_calls()[:-1], Step(calls=[("add_node", container)]), Step(text="done")]
    )
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    detail = str(state["outcome_detail_scrubbed"])
    events = await security_events(workspace, run_id)
    assert (
        final,
        "container_too_few_children" in detail,
        holds(detail, sample),
        "[REDACTED:slack-token]" in detail,
        [(e["exit"], e["rule"], e["fingerprint"]) for e in events],
    ) == ("rolled_back", True, False, True, [("run_record", "slack-token", fingerprint(sample))])


async def inbox_rows(ws: Workspace, process_id: str) -> str:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                "SELECT row_to_json(i)::text AS r FROM inbox_item i WHERE process_id = %s",
                (process_id,),
            )
        ).fetchall()
    return " ".join(str(r["r"]) for r in rows)


@CANARIES
async def test_an_agent_question_carrying_a_credential_is_correctable_and_never_stored(
    *, workspace: Workspace, registry: Registry, process_id: str, canary: str, rule: str
) -> None:
    await add_process_input(workspace, process_id)
    question = {"category": "spec-clarification", "question": f"Is {canary} the vendor's key?"}
    script = Script([Step(calls=[("inbox_ask", question)]), *section_steps("current")])
    run_id, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    events = await security_events(workspace, run_id)
    assert (
        final,
        holds(await inbox_rows(workspace, process_id), canary),
        "secret_in_content" in str(script.seen[1][-1]),
        [(e["exit"], e["rule"], e["fingerprint"]) for e in events],
    ) == ("committed", False, True, [("inbox", rule, fingerprint(canary))])
