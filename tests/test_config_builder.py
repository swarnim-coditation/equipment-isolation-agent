import argparse
import unittest

from config import ApiConfig, RunConfig
from pipeline.config_builder import build_run_config


class ConfigBuilderTests(unittest.TestCase):
    def test_build_run_config_applies_cli_equivalent_overrides(self):
        config = build_run_config(
            equipment_tag="BT-11",
            job_name="pnid_2_bio_final",
            job_id="2100",
            auth_token="token",
            api_base_url="https://api.example",
            verify_ssl=False,
            cnvrt_project_id="277",
            collection_id="206",
            collection_name="Test",
            host="janus",
            port="8182",
            project_id="15",
            traversal_source="graph15_traversal",
            max_depth=5,
            intrusive_work=False,
            high_risk_service=False,
            confined_space_entry=True,
            hot_work=True,
            output_dir="runs/abc",
        )
        self.assertEqual(config.equipment_tag, "BT-11")
        self.assertEqual(config.job_name, "pnid_2_bio_final")
        self.assertEqual(config.resolved_job_id, "2100")
        self.assertEqual(config.api, ApiConfig(base_url="https://api.example", auth_token="token", verify_ssl=False))
        self.assertEqual(config.cnvrt_project_id, "277")
        self.assertEqual(config.collection_id, "206")
        self.assertEqual(config.collection_name, "Test")
        self.assertEqual(config.graph.host, "janus")
        self.assertEqual(config.graph.port, "8182")
        self.assertEqual(config.graph.project_id, "15")
        self.assertEqual(config.graph.traversal_source, "graph15_traversal")
        self.assertEqual(config.policy.max_traversal_depth, 5)
        self.assertFalse(config.work_scope.intrusive_work)
        self.assertFalse(config.work_scope.high_risk_service)
        self.assertTrue(config.work_scope.confined_space_entry)
        self.assertTrue(config.work_scope.hot_work)
        self.assertEqual(str(config.output_dir), "runs/abc")

    def test_agent_cli_adapter_uses_shared_builder_defaults(self):
        from agent.cli import build_config

        args = argparse.Namespace(
            equipment="P3",
            job_name="",
            job_id="",
            project_config="project_config.json",
            project_profile="",
            auth_token="token",
            api_base_url="https://api.plant360.ai:8080",
            cnvrt_project_id="",
            collection_id="",
            collection_name="",
            host="",
            port="",
            project_id="",
            traversal_source="",
            max_depth=None,
            non_intrusive=False,
            not_high_risk=False,
            output_dir="output_agent",
        )
        self.assertIsInstance(build_config(args), RunConfig)


if __name__ == "__main__":
    unittest.main()
