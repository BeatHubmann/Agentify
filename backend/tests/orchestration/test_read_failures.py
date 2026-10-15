"""INV-10.SF (A§10.2): a transient artifact read failure is correctable; one that persists ends the
run through the error budget; neither is ever read as an absent artifact."""

import math
from typing import Any

import pytest
from botocore.exceptions import ClientError

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.blobs import ATTEMPTS, BlobStore
from agentify.store.workspace import Workspace
from tests.conftest import BUCKET, Infra, s3_client
from tests.orchestration.test_agent_layer import retry_errors
from tests.orchestration.test_run_exit_paths import (
    FLOW_PATH,
    commit_golden,
    head_flow,
    history,
    run_once,
)
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


class ReadFaults:
    """The real S3 client; once armed, reads of one key fail `remaining` times as unavailable."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.key = ""
        self.remaining: float = 0

    def get_object(self, **kwargs: Any) -> Any:
        if kwargs["Key"] == self.key and self.remaining > 0:
            self.remaining -= 1
            error = {"Error": {"Code": "ServiceUnavailable", "Message": "storage is busy"}}
            raise ClientError(error, "GetObject")  # type: ignore[arg-type]
        return self._real.get_object(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


async def flow_key(ws: Workspace, process_id: str) -> str:
    async with ws.db.tx() as conn:
        engagement = await ws.engagement_of(conn, process_id)
        paths = await ws.path_map(conn, await ws.head_revision(conn, process_id))
    return BlobStore.key(engagement, paths[FLOW_PATH])


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        (1, ("committed", [], "R")),
        (ATTEMPTS, ("committed", ["artifact_read_failed"], "R")),
        (math.inf, ("rolled_back", ["artifact_read_failed"] * 5, "receive")),
    ],
    ids=["absorbed-by-retry", "one-call-outlasted", "persistent"],
)
async def test_a_failing_read_of_the_base_flow_is_retried_reported_or_aborts_never_empty(
    *,
    infra: Infra,
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    failures: float,
    expected: tuple[str, list[str], str],
) -> None:
    await commit_golden(workspace, registry, process_id)
    faults = ReadFaults(s3_client(infra))
    faulty = Workspace(workspace.db, BlobStore(faults, BUCKET), workspace.gate)
    key = await flow_key(workspace, process_id)

    async def arm() -> None:
        faults.key, faults.remaining = key, failures

    relabel = ("edit_node", {"id": "receive", "label": "R"})
    steps = [Step(calls=[relabel], before=arm), *(Step(calls=[relabel]) for _ in range(9))]
    script = Script([*steps, Step(text="relabelled")])
    run_id, final = await run_once(faulty, registry, process_id, script, mode="incremental")
    state = await runs.run_state(workspace, run_id)
    head = await head_flow(workspace, process_id)
    assert head is not None
    assert (final, retry_errors(script), head.node_map()["receive"].label) == expected
    assert (state["outcome"], len(await history(workspace, process_id))) == (
        ("committed", 2) if final == "committed" else ("aborted", 1)
    )
