"""The harness gate for artifact-authoring kinds (A§8.6 "artifact gate"), and its commit validator.

Like the flow gate it re-reads stored bytes and never depends on anything the model called."""

from typing import TYPE_CHECKING

from agentify.artifacts import CheckContext, parse_body, run_checks
from agentify.domain.envelope import Corrupt, decode_artifact
from agentify.domain.validate import Violation
from agentify.harness.gate import TRUNCATED_FINISH_REASONS, GateResult
from agentify.registry import ArtifactAgentKind, Registry
from agentify.store.workspace import PathReader, Validator, Workspace

if TYPE_CHECKING:
    from agentify.harness.runs import ClaimedRun


async def validate_outputs(
    reader: PathReader, kind: ArtifactAgentKind, registry: Registry, paths: frozenset[str]
) -> list[Violation]:
    found: list[Violation] = []
    for output in kind.outputs:
        if output.path not in paths:
            continue
        artifact_type = registry.artifact_types[output.artifact_type]
        data = await reader.read(output.path)
        if data is None:
            found.append(
                Violation("missing_output", f"{output.path} was not produced.", (output.path,))
            )
            continue
        envelope = decode_artifact(data)
        if isinstance(envelope, Corrupt) or envelope.artifact_type != artifact_type.id:
            found.append(
                Violation("output_corrupt", f"{output.path} is unreadable.", (output.path,))
            )
            continue
        parsed = parse_body(artifact_type.schema_ref, envelope.body)
        if isinstance(parsed, list):
            found.extend(parsed)
            continue
        found.extend(await run_checks(artifact_type.gate_checks, parsed, CheckContext(reader.read)))
    return found


async def check(
    ws: Workspace,
    claimed: "ClaimedRun",
    kind: ArtifactAgentKind,
    registry: Registry,
    finish_reason: str | None,
) -> GateResult:
    run_id, process_id, incremental = (
        claimed.run_id,
        claimed.process_id,
        claimed.mode == "incremental",
    )
    if finish_reason in TRUNCATED_FINISH_REASONS:
        return GateResult(
            False,
            violations=(Violation("truncated_finish", "The model's last answer was cut off."),),
        )
    staged = await ws.staged(run_id)
    if not staged:
        if incremental:
            return GateResult(True, unchanged=True)
        return GateResult(
            False, violations=(Violation("nothing_staged", "No artifact was produced."),)
        )
    required = frozenset(staged) if incremental else frozenset(kind.output_paths)
    async with ws.db.tx() as conn:
        engagement_id = await ws.engagement_of(conn, process_id)
        head_map = await ws.path_map(conn, await ws.head_revision(conn, process_id))
    reader = PathReader(ws.blobs, engagement_id, {**head_map, **staged})
    violations = await validate_outputs(reader, kind, registry, required)
    return GateResult(not violations, violations=tuple(violations))


def commit_validator(kind: ArtifactAgentKind, registry: Registry) -> Validator:
    async def validate(reader: PathReader, changed: frozenset[str]) -> list[Violation]:
        return await validate_outputs(reader, kind, registry, changed)

    return validate
