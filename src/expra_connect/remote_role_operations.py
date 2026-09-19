"""Cluster role-operation adapter mixed into authenticated remote providers."""

from __future__ import annotations

from typing import Any


class RemoteRoleOperations:
    """Build fenced cluster requests without owning transport or authorization."""

    def consume_invite(
        self, token: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "consume_invite",
            {
                "token": token,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def assign_role(
        self,
        target_node_id: str,
        roles: list[str],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "assign_role",
            {
                "target_node_id": target_node_id,
                "roles": roles,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def renew_coordinator_lease(
        self, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "renew_coordinator_lease",
            {
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def upload_snapshot(
        self,
        payload: dict[str, Any],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "worker_snapshot",
            {
                "payload": payload,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def upload_standby_batch(
        self,
        payload: dict[str, Any],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "standby_batch",
            {
                "payload": payload,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def pause_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "pause_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def revoke_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "revoke_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def resume_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "resume_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def remove_connection(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "remove_connection",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def remove_job(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "remove_job",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def grant_capabilities(
        self,
        subject_node_id: str,
        target_node_id: str,
        permissions: list[str],
        *,
        expires_at: float,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "grant_capabilities",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "permissions": permissions,
                "expires_at": expires_at,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def revoke_capabilities(
        self,
        subject_node_id: str,
        target_node_id: str,
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "revoke_capabilities",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def sync_capability_grant(
        self,
        subject_node_id: str,
        target_node_id: str,
        permissions: list[str],
        *,
        expires_at: float,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "sync_capability_grant",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "permissions": permissions,
                "expires_at": expires_at,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def _role_request(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
