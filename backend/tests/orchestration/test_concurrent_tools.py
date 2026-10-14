"""HZ-9: several tool calls in one model response run concurrently; none of their writes is lost."""

import itertools

import pytest

from agentify.domain.envelope import decode_flow
from agentify.domain.flow import Flow
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import FLOW_PATH, run_once
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_parallel_tool_calls_in_one_response_all_land(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    ids = ["a", "b", "c", "d", "e", "f"]
    adds = [("add_node", {"id": i, "label": i, "kind": "step", "execution": "human"}) for i in ids]
    links = [
        ("add_edge", {"id": f"e-{x}-{y}", "source": x, "target": y})
        for x, y in itertools.pairwise(ids)
    ]
    _run_id, final = await run_once(
        workspace,
        registry,
        process_id,
        Script([Step(calls=adds), Step(calls=links), Step(text="done")]),
    )
    data = await workspace.read(process_id, FLOW_PATH)
    assert data is not None
    flow = decode_flow(data)
    assert isinstance(flow, Flow)
    assert (final, sorted(n.id for n in flow.nodes), len(flow.edges)) == ("committed", ids, 5)
