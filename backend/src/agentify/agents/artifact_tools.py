"""Generic tools for registry artifact types (A§6.12): read, and small validated JSON Patch edits.

Every `patch_artifact` call applies an RFC 6902 patch to the staged body, validates it against the
type's schema and its write checks, and stages it, or returns a structured, correctable error."""

import json
from dataclasses import dataclass, field
from typing import Any

import jsonpatch
from pydantic_ai import Agent, RunContext

from agentify.agents.flow_tools import FRAMEWORK_RETRIES
from agentify.agents.inbox_tools import register_inbox_tools
from agentify.artifacts import CheckContext, parse_body, run_checks
from agentify.domain.envelope import Corrupt, decode_artifact, encode
from agentify.harness.ports import ArtifactLocks, InboxPort, StagingPort, correctable
from agentify.registry import ArtifactType, OutputDecl

ARTIFACT_SCHEMA_VERSION = 1


@dataclass
class ArtifactToolDeps:
    staging: StagingPort
    outputs: dict[str, tuple[OutputDecl, ArtifactType]]
    readable_prefixes: tuple[str, ...]
    output_language: str
    from_scratch: bool
    inbox: InboxPort | None = None
    inbox_categories: tuple[str, ...] = ()
    locks: ArtifactLocks = field(default_factory=ArtifactLocks)


async def current_body(deps: ArtifactToolDeps, path: str) -> dict[str, Any]:
    decl, artifact_type = deps.outputs[path]
    data = await deps.staging.read(path)
    if data is None:
        return dict(decl.initial)
    envelope = decode_artifact(data)
    if isinstance(envelope, Corrupt) or envelope.artifact_type != artifact_type.id:
        raise correctable("read_failure", f"{path} is unreadable.", (path,))
    return dict(envelope.body)


def _readable(deps: ArtifactToolDeps, path: str) -> bool:
    return path in deps.outputs or any(
        path == p or (p.endswith("/") and path.startswith(p)) for p in deps.readable_prefixes
    )


def register_artifact_tools(agent: Agent[ArtifactToolDeps, str]) -> None:
    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def list_inputs(ctx: RunContext[ArtifactToolDeps]) -> list[str]:
        """List the uploaded input documents."""
        return await ctx.deps.staging.list_paths("inputs/")

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def read_artifact(ctx: RunContext[ArtifactToolDeps], path: str) -> str:
        """Read an input document or an artifact this run may read."""
        deps = ctx.deps
        if not _readable(deps, path):
            raise correctable("path_not_readable", f"{path} is not readable by this run.", (path,))
        if path in deps.outputs:
            return json.dumps(await current_body(deps, path), sort_keys=True, ensure_ascii=False)
        data = await deps.staging.read(path)
        if data is None:
            raise correctable("not_found", f"{path} does not exist.", (path,))
        return data.decode("utf-8", errors="replace")

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def patch_artifact(
        ctx: RunContext[ArtifactToolDeps], path: str, ops: list[dict[str, Any]]
    ) -> str:
        """Apply a small RFC 6902 JSON Patch (add/replace/remove/...) to one output artifact."""
        deps = ctx.deps
        if path not in deps.outputs:
            raise correctable(
                "undeclared_output",
                f"{path} is not an output of this run.",
                (path,),
                hint=f"Outputs: {sorted(deps.outputs)}",
            )
        _decl, artifact_type = deps.outputs[path]
        async with deps.locks.for_path(path):
            body = await current_body(deps, path)
            try:
                patched = jsonpatch.apply_patch(body, ops)
            except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException, TypeError) as exc:
                raise correctable(
                    "invalid_patch", str(exc), (path,), hint="Use RFC 6902 operations."
                ) from exc
            parsed = parse_body(artifact_type.schema_ref, patched)
            if isinstance(parsed, list):
                raise correctable(parsed[0].code, parsed[0].message, parsed[0].location)
            violations = await run_checks(
                artifact_type.write_checks, parsed, CheckContext(deps.staging.read)
            )
            if violations:
                raise correctable(
                    violations[0].code,
                    "; ".join(v.message for v in violations[:5]),
                    violations[0].location,
                )
            data = encode(
                artifact_type.id,
                ARTIFACT_SCHEMA_VERSION,
                parsed,
                output_language=deps.output_language,
            )
            await deps.staging.write(path, data)
        return "ok"


def build_artifact_agent(instructions: str) -> Agent[ArtifactToolDeps, str]:
    agent: Agent[ArtifactToolDeps, str] = Agent(
        deps_type=ArtifactToolDeps, output_type=str, instructions=instructions
    )
    register_artifact_tools(agent)
    register_inbox_tools(agent)
    return agent
