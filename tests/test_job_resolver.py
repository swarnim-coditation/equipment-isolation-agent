import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ApiConfig, RunConfig
from job_resolver import resolve_job_from_boundary


def boundary(pnid="Janusz-2"):
    return {
        "equipment_boundaries": [
            {
                "equipment": {"properties": {"pnid": pnid}},
                "components": [],
            }
        ]
    }


def config(**kwargs):
    api = kwargs.pop("api", ApiConfig(base_url=kwargs.pop("base_url", "https://api.plant360.ai:8080"), auth_token=kwargs.pop("auth_token", "token")))
    return RunConfig(equipment_tag="AP001", api=api, **kwargs)


class FakePlant360Client:
    paths = []
    responses = {}

    def __init__(self, api_config):
        self.api_config = api_config

    def get_json(self, path):
        self.__class__.paths.append(path)
        if path in self.__class__.responses:
            return self.__class__.responses[path]
        return {"results": [], "count": 0}


class JobResolverTests(unittest.TestCase):
    def setUp(self):
        FakePlant360Client.paths = []
        FakePlant360Client.responses = {}

    def test_configured_collection_resolves_from_nested_jobs_api(self):
        FakePlant360Client.responses[
            "/projects/277/collections/206/jobs?name=Janusz-2"
        ] = {"results": [{"id": 2481, "name": "Janusz-2", "project": 277, "collection": 206}]}

        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            resolved, debug = resolve_job_from_boundary(
                config(cnvrt_project_id="277", collection_id="206"),
                boundary(),
                cache_path=Path(tmp) / "jobs.json",
            )

        self.assertEqual(resolved.resolved_job_id, "2481")
        self.assertEqual(debug["job_resolution"], "project_collection_jobs_api")
        self.assertFalse(debug["fatal"])
        self.assertEqual(FakePlant360Client.paths, ["/projects/277/collections/206/jobs?name=Janusz-2"])

    def test_boundary_job_reference_resolves_when_job_is_in_metadata_map(self):
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            resolved, debug = resolve_job_from_boundary(
                config(
                    cnvrt_project_id="277",
                    collection_id="206",
                    job_ids_by_name={"PID-0134-1": "2152"},
                ),
                boundary("pnid:job:2152"),
                cache_path=Path(tmp) / "jobs.json",
            )

        self.assertEqual(resolved.resolved_job_id, "2152")
        self.assertEqual(resolved.job_name, "PID-0134-1")
        self.assertEqual(debug["job_resolution"], "boundary_job_reference")
        self.assertEqual(FakePlant360Client.paths, [])

    def test_boundary_job_reference_outside_metadata_map_is_fatal_for_configured_collection(self):
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            resolved, debug = resolve_job_from_boundary(
                config(
                    cnvrt_project_id="277",
                    collection_id="206",
                    job_ids_by_name={"PID-0134-1": "2152"},
                ),
                boundary("pnid:job:9999"),
                cache_path=Path(tmp) / "jobs.json",
            )

        self.assertEqual(resolved.resolved_job_id, "")
        self.assertTrue(debug["fatal"])
        self.assertEqual(debug["job_resolution_error"], "job_name_not_found_in_configured_collection")

    def test_configured_collection_miss_is_fatal_and_does_not_scan_global_jobs(self):
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            resolved, debug = resolve_job_from_boundary(
                config(cnvrt_project_id="277", collection_id="206"),
                boundary(),
                cache_path=Path(tmp) / "jobs.json",
            )

        self.assertEqual(resolved.resolved_job_id, "")
        self.assertTrue(debug["fatal"])
        self.assertEqual(debug["job_resolution_error"], "job_name_not_found_in_configured_collection")
        self.assertNotIn("/jobs?page=1&page_size=50", FakePlant360Client.paths)

    def test_cache_scope_includes_api_project_and_collection(self):
        cache_payload = {
            "scopes": {
                "https://api.plant360.ai:8080|project=277|collection=206": {
                    "jobs_by_name": {"Janusz-2": "2481"}
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            cache_path = Path(tmp) / "jobs.json"
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
            resolved, debug = resolve_job_from_boundary(
                config(cnvrt_project_id="277", collection_id="999"),
                boundary(),
                cache_path=cache_path,
            )

        self.assertEqual(resolved.resolved_job_id, "")
        self.assertTrue(debug["fatal"])
        self.assertEqual(FakePlant360Client.paths, ["/projects/277/collections/999/jobs?name=Janusz-2"])

    def test_api_base_url_difference_uses_different_cache_scope(self):
        cache_payload = {
            "scopes": {
                "https://api.plant360.ai:8080|project=277|collection=206": {
                    "jobs_by_name": {"Janusz-2": "2481"}
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            cache_path = Path(tmp) / "jobs.json"
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
            resolved, debug = resolve_job_from_boundary(
                config(base_url="https://api.other.example", cnvrt_project_id="277", collection_id="206"),
                boundary(),
                cache_path=cache_path,
            )

        self.assertEqual(resolved.resolved_job_id, "")
        self.assertTrue(debug["fatal"])
        self.assertEqual(FakePlant360Client.paths, ["/projects/277/collections/206/jobs?name=Janusz-2"])

    def test_legacy_no_configured_collection_can_scan_global_jobs(self):
        FakePlant360Client.responses["/jobs?page=1&page_size=50"] = {
            "count": 1,
            "results": [{"id": 999, "name": "Janusz-2"}],
        }
        with tempfile.TemporaryDirectory() as tmp, patch("job_resolver.Plant360Client", FakePlant360Client):
            resolved, debug = resolve_job_from_boundary(
                config(cnvrt_project_id="", collection_id="206"),
                boundary(),
                cache_path=Path(tmp) / "jobs.json",
            )

        self.assertEqual(resolved.resolved_job_id, "999")
        self.assertEqual(debug["job_resolution"], "jobs_scan")
        self.assertFalse(debug["fatal"])

    def test_missing_pnid_is_non_fatal_unavailable(self):
        resolved, debug = resolve_job_from_boundary(config(cnvrt_project_id="277", collection_id="206"), boundary(""))

        self.assertEqual(resolved.resolved_job_id, "")
        self.assertEqual(debug["job_resolution_error"], "missing_pnid_name")
        self.assertFalse(debug["fatal"])


if __name__ == "__main__":
    unittest.main()
