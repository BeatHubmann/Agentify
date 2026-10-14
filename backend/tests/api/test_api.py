"""API: authentication, segregation (service checks and row-level security), roles, attribution,
error bodies and logs through the secret gate (A§18, A§19, OD-7, OD-8)."""

import io

import httpx
import psycopg
import pytest

from agentify import inbox
from agentify.observability import configure_logging
from agentify.secretgate import SecretGate
from agentify.store.db import engagement_scope
from tests.api.conftest import (
    ADMIN_SUBJECT,
    TokenFactory,
    bearer,
    engagement_with,
    sign_in,
    subject,
)
from tests.conftest import KNOWN_SECRET, Infra
from tests.support.agents import INPUT_TEXT

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def process_for(api: httpx.AsyncClient, token: TokenFactory) -> dict[str, object]:
    consultant = subject()
    setup = await engagement_with(api, token, {consultant: "consultant"})
    headers = setup["users"][consultant][1]
    created = await api.post(
        f"/engagements/{setup['engagement_id']}/processes",
        json={"name": "Invoices"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return {
        **setup,
        "process_id": created.json()["process_id"],
        "headers": headers,
        "user_id": setup["users"][consultant][0],
    }


@pytest.mark.parametrize(
    "case", ["missing", "wrong-signature", "wrong-audience", "wrong-issuer", "expired"]
)
async def test_requests_without_a_valid_token_are_refused(
    api: httpx.AsyncClient, token: TokenFactory, case: str
) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    tokens = {
        "missing": None,
        "wrong-signature": token(
            "someone", key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ),
        "wrong-audience": token("someone", audience="another-api"),
        "wrong-issuer": token("someone", issuer="https://elsewhere.invalid/"),
        "expired": token("someone", expires_in=-3600),
    }
    value = tokens[case]
    response = await api.get("/me", headers=bearer(value) if value else {})
    assert (response.status_code, response.json()["code"]) == (401, "unauthenticated")


async def test_first_sign_in_creates_one_user_per_subject(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    who = subject()
    first, _ = await sign_in(api, token, who)
    again, _ = await sign_in(api, token, who)
    admin = (await api.get("/me", headers=bearer(token(ADMIN_SUBJECT)))).json()
    assert (first == again, first.startswith("usr_"), admin["is_admin"]) == (True, True, True)


async def test_a_consultant_works_on_their_process_and_every_change_is_attributed(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    uploaded = await api.post(
        f"/processes/{pid}/inputs", json={"name": "process.md", "text": INPUT_TEXT}, headers=headers
    )
    revisions = (await api.get(f"/processes/{pid}/revisions", headers=headers)).json()
    stored = await api.get(
        f"/processes/{pid}/artifact", params={"path": "inputs/process.md"}, headers=headers
    )
    assert (
        uploaded.status_code,
        [(r["author_kind"], r["author_id"]) for r in revisions],
        stored.text,
    ) == (201, [("user", setup["user_id"])], INPUT_TEXT)


async def test_a_member_of_one_engagement_cannot_reach_another_engagements_process(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    mine = await process_for(api, token)
    theirs = await process_for(api, token)
    pid = theirs["process_id"]
    reads = [
        (await api.get(f"/processes/{pid}", headers=mine["headers"])).status_code,
        (await api.get(f"/processes/{pid}/revisions", headers=mine["headers"])).status_code,
        (
            await api.post(
                f"/processes/{pid}/inputs",
                json={"name": "x.md", "text": "x"},
                headers=mine["headers"],
            )
        ).status_code,
        (
            await api.post(
                f"/engagements/{theirs['engagement_id']}/processes",
                json={"name": "y"},
                headers=mine["headers"],
            )
        ).status_code,
    ]
    assert reads == [404, 404, 404, 404]


async def test_row_level_security_is_a_second_wall_for_the_api_role(
    infra: Infra, api: httpx.AsyncClient, token: TokenFactory
) -> None:
    a = await process_for(api, token)
    b = await process_for(api, token)
    seen: dict[str, int] = {}
    async with await psycopg.AsyncConnection.connect(infra.api_conninfo) as conn:
        for label, scope in (
            ("own scope", a["engagement_id"]),
            ("other scope", b["engagement_id"]),
            ("no scope", None),
        ):
            async with conn.transaction():
                if scope is not None:
                    await conn.execute(
                        "SELECT set_config('agentify.engagement_ids', %s, true)", (scope,)
                    )
                row = await (
                    await conn.execute(
                        "SELECT count(*) FROM process WHERE id = %s", (a["process_id"],)
                    )
                ).fetchone()
                seen[label] = int(row[0]) if row else -1
        refused = None
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('agentify.engagement_ids', %s, true)", (b["engagement_id"],)
                )
                await conn.execute(
                    "INSERT INTO workspace_head (process_id) VALUES (%s)", (a["process_id"],)
                )
        except psycopg.errors.InsufficientPrivilege as exc:
            refused = type(exc).__name__
    assert (seen, refused) == (
        {"own scope": 1, "other scope": 0, "no scope": 0},
        "InsufficientPrivilege",
    )


async def test_roles_limit_what_a_member_may_do(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    setup = await process_for(api, token)
    reviewer = subject()
    reviewer_id, reviewer_headers = await sign_in(api, token, reviewer)
    await api.post(
        f"/engagements/{setup['engagement_id']}/members",
        json={"user_id": reviewer_id, "role": "reviewer"},
        headers=setup["admin"],
    )
    pid = setup["process_id"]
    results = [
        (await api.get(f"/processes/{pid}", headers=reviewer_headers)).status_code,
        (
            await api.post(
                f"/processes/{pid}/inputs",
                json={"name": "r.md", "text": "r"},
                headers=reviewer_headers,
            )
        ).status_code,
        (
            await api.post("/engagements", json={"name": "not allowed"}, headers=reviewer_headers)
        ).status_code,
    ]
    assert results == [200, 403, 403]


async def test_runs_are_admitted_cancelled_and_refused_through_the_api(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    body = {"kind": "current-state-modeler", "mode": "from_scratch", "instructions": "model it"}
    first = await api.post(f"/processes/{pid}/runs", json=body, headers=headers)
    second = await api.post(f"/processes/{pid}/runs", json=body, headers=headers)
    run_id = first.json()["run_id"]
    cancelled = await api.post(f"/runs/{run_id}/cancel", headers=headers)
    state = (await api.get(f"/runs/{run_id}", headers=headers)).json()
    assert (
        first.status_code,
        second.status_code,
        second.json()["code"],
        cancelled.status_code,
        state["initiated_by"],
        state["entry_point"],
    ) == (201, 409, "already_running", 202, setup["user_id"], "api")


async def test_inbox_answers_and_confirmations_are_attributed_to_the_signed_in_user(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    ctx = api._transport.app.state.ctx  # type: ignore[attr-defined]
    with engagement_scope((str(setup["engagement_id"]),)):
        item_id = await inbox.ask(
            ctx.ws,
            ctx.registry,
            process_id=pid,
            category="spec-clarification",
            question="Who approves the fee?",
            options=["clerk", "manager"],
            run_id=None,
        )
    answered = await api.post(
        f"/processes/{pid}/inbox/{item_id}/answer", json={"text": "clerk"}, headers=headers
    )
    items = (await api.get(f"/processes/{pid}/inbox", headers=headers)).json()
    confirmation = await api.post(
        f"/processes/{pid}/confirmations",
        json={"run_kind": "current-state-modeler"},
        headers=headers,
    )
    answer = next(i for i in items if i["parent_item_id"] == item_id)
    assert (
        answered.status_code,
        (answer["author_kind"], answer["author_id"]),
        confirmation.status_code,
    ) == (201, ("user", setup["user_id"]), 201)


async def test_error_bodies_and_logs_never_carry_a_credential(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    captured = io.StringIO()
    configure_logging(SecretGate([KNOWN_SECRET]), stream=captured)
    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    bad_name = await api.post(
        f"/processes/{pid}/inputs", json={"name": f"{KNOWN_SECRET}/x", "text": "x"}, headers=headers
    )
    secret_text = await api.post(
        f"/processes/{pid}/inputs",
        json={"name": "doc.md", "text": f"key={KNOWN_SECRET}"},
        headers=headers,
    )
    await api.get(f"/processes/{KNOWN_SECRET}", headers=headers)
    stored = await api.get(
        f"/processes/{pid}/artifact", params={"path": "inputs/doc.md"}, headers=headers
    )
    logs = captured.getvalue()
    assert (
        bad_name.status_code,
        KNOWN_SECRET in bad_name.text,
        secret_text.status_code,
        secret_text.json()["code"],
        stored.status_code,
        KNOWN_SECRET in logs,
        "[REDACTED" in logs,
    ) == (400, False, 422, "secret_detected", 404, False, True)


async def test_edit_batches_through_the_api_commit_conflict_or_are_rejected(
    api: httpx.AsyncClient, token: TokenFactory, workspace: object, registry: object
) -> None:

    from tests.orchestration.test_run_exit_paths import commit_golden

    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    await commit_golden(workspace, registry, str(pid))  # type: ignore[arg-type]
    base = (await api.get(f"/processes/{pid}", headers=headers)).json()["head_revision_id"]
    relabel = {"op": "modify_node", "target": "approve", "payload": {"label": "Approve invoice"}}
    edit = {"kind": "current-state-modeler", "base_revision_id": base, "patches": [relabel]}
    first = await api.post(f"/processes/{pid}/edits", json=edit, headers=headers)
    stale = await api.post(f"/processes/{pid}/edits", json=edit, headers=headers)
    head = (await api.get(f"/processes/{pid}", headers=headers)).json()["head_revision_id"]
    breaking = {
        "kind": "current-state-modeler",
        "base_revision_id": head,
        "patches": [{"op": "modify_node", "target": "approve", "payload": {"kind": "task"}}],
    }
    invalid = await api.post(f"/processes/{pid}/edits", json=breaking, headers=headers)
    revisions = (await api.get(f"/processes/{pid}/revisions", headers=headers)).json()
    assert (
        first.status_code,
        stale.status_code,
        stale.json()["code"],
        invalid.status_code,
        invalid.json()["code"],
        revisions[-1]["author_id"],
    ) == (201, 409, "edit_conflict", 422, "archetype_requires_ai", setup["user_id"])


async def test_an_unhandled_error_never_echoes_its_message_in_the_body_or_the_logs(
    api: httpx.AsyncClient, token: TokenFactory
) -> None:
    captured = io.StringIO()
    configure_logging(SecretGate([KNOWN_SECRET]), stream=captured)
    app = api._transport.app  # type: ignore[attr-defined]

    @app.get("/test-only/boom")
    async def boom() -> None:
        raise RuntimeError(f"connection string contains {KNOWN_SECRET}")

    response = await api.get("/test-only/boom")
    assert (
        response.status_code,
        response.json()["code"],
        KNOWN_SECRET in response.text,
        KNOWN_SECRET in captured.getvalue(),
    ) == (500, "internal_error", False, False)


async def test_a_user_edit_carrying_a_credential_is_refused_without_echoing_it(
    api: httpx.AsyncClient, token: TokenFactory, workspace: object, registry: object
) -> None:
    from tests.orchestration.test_run_exit_paths import commit_golden

    setup = await process_for(api, token)
    pid, headers = setup["process_id"], setup["headers"]
    await commit_golden(workspace, registry, str(pid))  # type: ignore[arg-type]
    base = (await api.get(f"/processes/{pid}", headers=headers)).json()["head_revision_id"]
    edit = {
        "kind": "current-state-modeler",
        "base_revision_id": base,
        "patches": [
            {"op": "modify_node", "target": "approve", "payload": {"label": f"use {KNOWN_SECRET}"}}
        ],
    }
    response = await api.post(f"/processes/{pid}/edits", json=edit, headers=headers)
    revisions = (await api.get(f"/processes/{pid}/revisions", headers=headers)).json()
    assert (
        response.status_code,
        response.json()["code"],
        KNOWN_SECRET in response.text,
        len(revisions),
    ) == (422, "secret_detected", False, 1)


async def test_release_one_exposes_no_export_or_sign_off_endpoint(api: httpx.AsyncClient) -> None:
    paths = (await api.get("/openapi.json")).json()["paths"]
    assert [p for p in paths if "export" in p or "sign" in p] == []
