import unittest
from unittest.mock import patch

from config import ApiConfig, GraphConfig, RunConfig
from unigraph_metadata import enrich_config_from_unigraph


class FakePlant360Client:
    paths = []
    responses = {}

    def __init__(self, api_config):
        self.api_config = api_config

    def get_json(self, path):
        self.__class__.paths.append(path)
        if path not in self.__class__.responses:
            raise RuntimeError(f"missing fake response for {path}")
        return self.__class__.responses[path]


class UnigraphMetadataTests(unittest.TestCase):
    def setUp(self):
        FakePlant360Client.paths = []
        FakePlant360Client.responses = {
            "/api/projects/15": {"id": 15, "name": "Project 15", "cnvrt_project_id": 277},
            "/api/projects/99": {"id": 99, "name": "Wrong Project", "cnvrt_project_id": 999},
            "/api/projects/by-cnvrt?cnvrt_project_id=277": [
                {"id": 13, "name": "Project 13", "cnvrt_project_id": 277},
                {"id": 15, "name": "Project 15", "cnvrt_project_id": 277},
            ],
            "/api/projects/15/collections": [
                {"id": 57, "name": "Test", "cnvrt_collection_id": 206, "export_type": "project_export"}
            ],
            "/api/projects/15/collections/57/pnids": [{"id": 223}, {"id": 224}],
            "/api/projects/15/pnids/223/direction-review": {
                "pnid_id": 223,
                "pnid_name": "PID-0134-1",
                "cnvrt_job_id": 2152,
                "direction_summary": {"pending_choices": 0},
            },
            "/api/projects/15/pnids/224/direction-review": {
                "pnid_id": 224,
                "pnid_name": "Dadon-2",
                "cnvrt_job_id": 2483,
                "direction_summary": {"pending_choices": 0},
            },
        }

    def config(self, project_id="15", collection_id="206"):
        return RunConfig(
            equipment_tag="NEW",
            cnvrt_project_id="277",
            collection_id=collection_id,
            graph=GraphConfig(project_id=project_id),
            api=ApiConfig(auth_token="token"),
        )

    def test_loads_job_map_from_configured_unigraph_collection(self):
        with patch("unigraph_metadata.Plant360Client", FakePlant360Client):
            config, debug = enrich_config_from_unigraph(self.config())

        self.assertEqual(debug["status"], "completed")
        self.assertEqual(config.job_ids_by_name["PID-0134-1"], "2152")
        self.assertEqual(config.job_ids_by_name["Dadon-2"], "2483")
        self.assertIn("/api/projects/15/pnids/223/direction-review", FakePlant360Client.paths)

    def test_wrong_unigraph_project_for_cnvrt_project_is_fatal(self):
        with patch("unigraph_metadata.Plant360Client", FakePlant360Client):
            _config, debug = enrich_config_from_unigraph(self.config(project_id="99"))

        self.assertTrue(debug["fatal"])
        self.assertEqual(debug["error"], "configured_unigraph_project_not_linked_to_cnvrt_project")

    def test_missing_configured_collection_is_fatal(self):
        with patch("unigraph_metadata.Plant360Client", FakePlant360Client):
            _config, debug = enrich_config_from_unigraph(self.config(collection_id="999"))

        self.assertTrue(debug["fatal"])
        self.assertEqual(debug["error"], "configured_cnvrt_collection_not_found_in_unigraph_project")

    def test_wrapped_collection_and_pnid_responses_are_supported(self):
        FakePlant360Client.responses["/api/projects/15/collections"] = {
            "collections": [
                {"id": 57, "name": "Test", "cnvrt_collection_id": 206, "export_type": "project_export"}
            ]
        }
        FakePlant360Client.responses["/api/projects/15/collections/57/pnids"] = {"pnids": [{"id": 223}]}

        with patch("unigraph_metadata.Plant360Client", FakePlant360Client):
            config, debug = enrich_config_from_unigraph(self.config())

        self.assertEqual(debug["status"], "completed")
        self.assertEqual(config.job_ids_by_name["PID-0134-1"], "2152")


if __name__ == "__main__":
    unittest.main()
