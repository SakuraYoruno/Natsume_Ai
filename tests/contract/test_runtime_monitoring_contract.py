import unittest

from host_brain.app.main import (
    OBS_LANGSMITH_API_KEY_MISSING,
    OBS_LANGSMITH_DISABLED,
    build_agent_monitoring_profile,
    build_runtime_console_profile,
)


class TestRuntimeMonitoringContract(unittest.TestCase):
    def test_t_obs_02_langsmith_disabled_is_stable(self) -> None:
        profile = build_agent_monitoring_profile(env={"LANGSMITH_TRACING": "false"})
        self.assertEqual("langsmith", profile["provider"])
        self.assertEqual("DISABLED", profile["status"])
        self.assertEqual(OBS_LANGSMITH_DISABLED, profile["reason_code"])

    def test_t_obs_02_langsmith_requires_api_key_when_enabled(self) -> None:
        profile = build_agent_monitoring_profile(
            env={
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_PROJECT": "Natsume-dev",
                "LANGSMITH_API_KEY": "",
            }
        )
        self.assertIn(profile["status"], {"DEGRADED", "ACTIVE"})
        if profile["status"] == "DEGRADED":
            self.assertIn(profile["reason_code"], {OBS_LANGSMITH_API_KEY_MISSING, "OBS_LANGSMITH_UNAVAILABLE"})

    def test_t_obs_02_console_inline_default(self) -> None:
        profile = build_runtime_console_profile(spawn_console=False)
        self.assertEqual("inline", profile["console_mode"])
        self.assertFalse(profile["spawned"])


if __name__ == "__main__":
    unittest.main()
