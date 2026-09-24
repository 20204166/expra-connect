"""Pydantic models returned by the MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DependencyEvidence(StrictModel):
    name: str
    available: bool
    version: str | None = None
    error: str | None = None


class WorkspaceDoctorResult(StrictModel):
    status: Literal["READY", "READY_WITH_LIMITATIONS", "NOT_READY"]
    repository_root: str
    configured_python: str
    python_fallback: bool
    python_fallback_reason: str | None = None
    python_version: str | None = None
    imported_expra_connect: str | None = None
    expra_connect_version: str | None = None
    source_root: str
    source_root_match: bool
    git_revision: str | None = None
    git_dirty: bool | None = None
    dependencies: list[DependencyEvidence] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = False
    executed_user_code: bool = False


class SourceReadResult(StrictModel):
    source_id: str
    path: str
    content: str
    bytes_read: int
    truncated: bool
    read_only: bool = True
    executed_network_code: bool = False
    executed_user_code: bool = False


class SourceMatch(StrictModel):
    path: str
    line: int
    text: str


class SourceSearchResult(StrictModel):
    source_id: str
    query: str
    matches: list[SourceMatch]
    truncated: bool
    read_only: bool = True


class RepoInspectResult(StrictModel):
    repository_root: str
    top_level_entries: list[str]
    python_files: int
    documentation_files: int
    git_revision: str | None
    git_dirty: bool | None
    read_only: bool = True


class CheckResult(StrictModel):
    profile: str
    command: list[str]
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


class InspectionResult(StrictModel):
    action: str
    status: Literal["IMPLEMENTED", "PARTIAL", "TEST_ONLY", "DEFERRED", "NOT_PRESENT"]
    execution_mode: Literal["STATIC", "LOOPBACK_EXECUTION", "LAN_EXECUTION"]
    evidence: dict[str, object]
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = False
    executed_user_code: bool = False


class ProbeStage(StrictModel):
    stage: str
    outcome: Literal["PASS", "FAIL", "NOT_RUN", "SKIPPED"]
    detail: str | None = None


class ProbeResult(StrictModel):
    probe: Literal["discovery", "transport", "connection"]
    action: str
    status: Literal["PASS", "FAIL", "PARTIAL", "NOT_PRESENT"]
    execution_mode: Literal["STATIC", "LOOPBACK_EXECUTION", "LAN_EXECUTION"]
    stages: list[ProbeStage] = Field(default_factory=list)
    evidence: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = False
    executed_user_code: bool = False


class ScenarioEvent(StrictModel):
    sequence: int
    node: str
    event: str
    outcome: Literal["PASS", "FAIL", "NOT_RUN"]
    detail: str | None = None


class ScenarioNode(StrictModel):
    name: str
    node_id: str | None = None
    state: str
    bound_port: int | None = None
    connection_count: int = 0
    trusted_peer_count: int = 0


class ScenarioResult(StrictModel):
    scenario: Literal["pair_reconnect", "endpoint_change", "peer_restart"]
    status: Literal["PASS", "FAIL", "PARTIAL", "NOT_PRESENT"]
    execution_mode: Literal["LOOPBACK_EXECUTION"]
    nodes: list[ScenarioNode] = Field(default_factory=list)
    timeline: list[ScenarioEvent] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = True
    executed_user_code: bool = False


class SecurityFinding(StrictModel):
    name: Literal[
        "wrong_fingerprint",
        "revoked_peer",
        "unauthorized_capability",
        "stale_transaction",
    ]
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    expected_denial: bool
    observed_denial: bool
    observed_capabilities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class SecurityAuditResult(StrictModel):
    action: Literal[
        "all",
        "wrong_fingerprint",
        "revoked_peer",
        "unauthorized_capability",
        "stale_transaction",
    ]
    status: Literal["PASS", "FAIL", "PARTIAL", "NOT_PRESENT"]
    execution_mode: Literal["LOOPBACK_EXECUTION"]
    findings: list[SecurityFinding] = Field(default_factory=list)
    timeline: list[ScenarioEvent] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = True
    executed_user_code: bool = False


class ClusterMemberEvidence(StrictModel):
    node_id: str
    roles: list[str]
    online: bool
    paused: bool
    revoked: bool


class ClusterFailoverEvidence(StrictModel):
    old_coordinator_id: str
    new_coordinator_id: str
    old_epoch: int
    new_epoch: int
    old_fencing_token_present: bool
    new_fencing_token_present: bool
    fencing_token_values_omitted: bool
    stale_heartbeat_rejected: bool
    duplicate_promotion_rejected: bool
    returning_coordinator_role: str


class ClusterAuditResult(StrictModel):
    action: Literal["inspect", "failover"]
    status: Literal["PASS", "FAIL", "PARTIAL", "NOT_PRESENT"]
    execution_mode: Literal["LOOPBACK_EXECUTION"]
    cluster_id: str
    coordinator_id: str
    local_role: str
    epoch: int
    fencing_token_present: bool
    fencing_token_values_omitted: bool
    members: list[ClusterMemberEvidence] = Field(default_factory=list)
    persisted_round_trip: bool
    failover: ClusterFailoverEvidence | None = None
    timeline: list[ScenarioEvent] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = False
    executed_user_code: bool = False


class LatencyEvidence(StrictModel):
    name: Literal["discovery", "connect", "reconnect"]
    sample_count: int
    warmup_count: int
    distribution: dict[str, float | int]
    measurement_scope: str


class RetryEvidence(StrictModel):
    attempts: list[int]
    delays_seconds: list[float]
    authentication_disables_retry: bool
    cadence_source: str


class RetentionEvidence(StrictModel):
    threads_before: int
    threads_after: int
    tasks_before: int
    tasks_after: int
    sockets_before: int
    sockets_after: int
    registry_before: int
    registry_after: int
    worker_processes_reaped: bool
    bounded: bool


class PerformanceAuditResult(StrictModel):
    action: Literal["all", "latency", "retry", "retention"]
    status: Literal["PASS", "FAIL", "PARTIAL", "NOT_PRESENT"]
    execution_mode: Literal["LOOPBACK_EXECUTION"]
    latencies: list[LatencyEvidence] = Field(default_factory=list)
    retry: RetryEvidence
    retention: RetentionEvidence
    diagnostics: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    executed_network_code: bool = True
    executed_user_code: bool = False
