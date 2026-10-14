"""The canonical minimal case: its expectations record and the scripted golden flow agree."""

from pathlib import Path

import yaml

from agentify.domain.semantics import end_nodes, start_nodes
from tests.support.flows import minimal_golden

CASE = Path(__file__).resolve().parents[3] / "evals" / "synthetic" / "minimal-linear-4"


def test_minimal_golden_flow_meets_its_expectations_record() -> None:
    expect = yaml.safe_load((CASE / "expectations.yaml").read_text())["expect"]
    flow = minimal_golden()
    assert (
        expect["node_count"]["min"] <= len(flow.nodes) <= expect["node_count"]["max"],
        [n.execution.value for n in flow.nodes],
        len(start_nodes(flow)) == len(end_nodes(flow)) == 1
        and len(flow.edges) == len(flow.nodes) - 1,
    ) == (True, expect["executions"], expect["linear"])
