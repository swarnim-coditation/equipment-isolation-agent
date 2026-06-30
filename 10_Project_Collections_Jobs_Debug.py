import json

import httpx
from pydantic.v1 import SecretStr

from langflow.custom import Component
from langflow.io import BoolInput, MessageTextInput, Output, SecretStrInput
from langflow.schema import Data, Message


class ProjectCollectionsJobsDebug(Component):
    display_name = "Project Collections Jobs Debug"
    description = "Lists project collections and jobs for diagnosing P&ID graph context"
    icon = "list-tree"
    name = "ProjectCollectionsJobsDebug"

    inputs = [
        MessageTextInput(
            name="api_base_url",
            display_name="API Base URL",
            value="https://api.plant360.ai:8080",
        ),
        SecretStrInput(
            name="auth_token",
            display_name="Authentication Token",
            info="Bearer token for Plant360 API access",
            required=True,
            value="",
        ),
        MessageTextInput(
            name="project_id",
            display_name="Project ID",
            value="274",
        ),
        MessageTextInput(
            name="target_job_id",
            display_name="Target Job ID",
            info="Optional job id to highlight in the grouped result",
            value="",
        ),
        BoolInput(
            name="verify_ssl",
            display_name="Verify SSL",
            value=False,
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="build_data"),
        Output(display_name="Summary", name="summary", method="build_summary"),
    ]

    def _headers(self):
        token = SecretStr(self.auth_token).get_secret_value()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _get(self, client, path, params=None):
        url = f"{self.api_base_url.rstrip('/')}/{path.lstrip('/')}"
        response = client.get(url, headers=self._headers(), params=params or {})
        response.raise_for_status()
        body = response.json()
        return body

    def _as_list(self, value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("results", "data", "collections", "jobs"):
                if isinstance(value.get(key), list):
                    return value.get(key)
        return []

    def _collection_id(self, collection):
        return collection.get("id") or collection.get("collection_id")

    def _job_collection_id(self, job):
        collection = job.get("collection_id") or job.get("collection")
        if isinstance(collection, dict):
            return collection.get("id")
        return collection

    def _build_payload(self):
        project_id = str(self.project_id).strip()
        target_job_id = str(self.target_job_id).strip()

        payload = {
            "error": False,
            "project_id": project_id,
            "target_job_id": target_job_id,
            "collections": [],
            "jobs": [],
            "jobs_by_collection": [],
            "target_job_matches": [],
            "endpoint_errors": [],
        }

        with httpx.Client(verify=bool(self.verify_ssl), timeout=60) as client:
            try:
                collections_body = self._get(client, f"projects/{project_id}/collections")
                payload["collections"] = self._as_list(collections_body)
            except Exception as exc:
                payload["endpoint_errors"].append(
                    {"endpoint": f"/projects/{project_id}/collections", "error": str(exc)}
                )

            try:
                jobs_body = self._get(client, "jobs/list_complete", params={"project_id": project_id})
                payload["jobs"] = self._as_list(jobs_body)
            except Exception as exc:
                payload["endpoint_errors"].append(
                    {"endpoint": f"/jobs/list_complete?project_id={project_id}", "error": str(exc)}
                )

        collections_by_id = {}
        for collection in payload["collections"]:
            if isinstance(collection, dict):
                collections_by_id[str(self._collection_id(collection))] = collection

        jobs_by_collection = {}
        for job in payload["jobs"]:
            if not isinstance(job, dict):
                continue
            collection_id = self._job_collection_id(job)
            key = str(collection_id)
            jobs_by_collection.setdefault(key, []).append(job)
            if target_job_id and str(job.get("id")) == target_job_id:
                payload["target_job_matches"].append(job)

        grouped = []
        for collection_id, jobs in sorted(jobs_by_collection.items(), key=lambda item: item[0]):
            collection = collections_by_id.get(collection_id) or {}
            grouped.append(
                {
                    "collection_id": None if collection_id == "None" else collection_id,
                    "collection_name": collection.get("name"),
                    "collection": collection,
                    "job_count": len(jobs),
                    "jobs": jobs,
                }
            )

        collection_ids_with_jobs = {item["collection_id"] for item in grouped}
        for collection_id, collection in sorted(collections_by_id.items(), key=lambda item: item[0]):
            normalized_id = None if collection_id == "None" else collection_id
            if normalized_id in collection_ids_with_jobs:
                continue
            grouped.append(
                {
                    "collection_id": normalized_id,
                    "collection_name": collection.get("name"),
                    "collection": collection,
                    "job_count": 0,
                    "jobs": [],
                }
            )

        payload["jobs_by_collection"] = grouped
        payload["collection_count"] = len(payload["collections"])
        payload["job_count"] = len(payload["jobs"])

        return payload

    def build_data(self) -> Data:
        try:
            return Data(value=self._build_payload())
        except Exception as exc:
            return Data(value={"error": True, "message": str(exc)})

    def build_summary(self) -> Message:
        data = self._build_payload()
        lines = [
            f"Project ID: {data.get('project_id')}",
            f"Collections: {data.get('collection_count', 0)}",
            f"Jobs: {data.get('job_count', 0)}",
            f"Target job matches: {len(data.get('target_job_matches', []))}",
            "",
        ]
        for group in data.get("jobs_by_collection", []):
            lines.append(
                f"Collection {group.get('collection_id')} - {group.get('collection_name') or '(unknown)'}: {group.get('job_count')} job(s)"
            )
            for job in group.get("jobs", [])[:20]:
                marker = " <-- target" if str(job.get("id")) == str(data.get("target_job_id")) else ""
                lines.append(f"  Job {job.get('id')}: {job.get('name')}{marker}")
        if data.get("endpoint_errors"):
            lines.extend(["", "Endpoint errors:", json.dumps(data.get("endpoint_errors"), indent=2)])
        return Message(text="\n".join(lines))
