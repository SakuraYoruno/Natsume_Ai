import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EDGE_FIXTURES = ROOT / "tests" / "fixtures" / "edge"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*_(accept|reject|degraded)\.json$")


class TestEdgeFixtureMatrixContract(unittest.TestCase):
    def test_task_0089_fixture_name_pattern_is_stable(self) -> None:
        categories = ("auth", "gateway", "health", "voice", "start")
        for category in categories:
            for row in (EDGE_FIXTURES / category).rglob("*.json"):
                self.assertRegex(
                    row.name,
                    NAME_PATTERN,
                    msg=f"invalid fixture name: {row}",
                )

    def test_task_0089_t_edge_matrix_has_success_and_reject_or_degraded_samples(self) -> None:
        def _glob_count(pattern: str) -> int:
            return len(list(EDGE_FIXTURES.glob(pattern)))

        # T-Edge-RPC-01
        self.assertGreater(_glob_count("auth/good/*_accept.json"), 0)
        self.assertGreater(_glob_count("auth/bad/*_reject.json"), 0)

        # T-Edge-RPC-02
        self.assertGreater(_glob_count("gateway/good/*_accept.json"), 0)
        self.assertGreater(_glob_count("gateway/bad/*_reject.json"), 0)

        # T-Edge-Health-01 (accept + degraded/reject)
        self.assertGreater(_glob_count("health/good/*_accept.json"), 0)
        self.assertTrue(
            _glob_count("health/good/*_degraded.json") > 0
            or _glob_count("health/bad/*_reject.json") > 0
        )

        # T-Edge-Start-01
        self.assertGreater(_glob_count("start/good/*_accept.json"), 0)
        self.assertGreater(_glob_count("start/bad/*_reject.json"), 0)

        # T-Edge-Voice-01
        self.assertGreater(_glob_count("voice/good/*asr*_accept.json"), 0)
        self.assertGreater(_glob_count("voice/bad/*asr*_reject.json"), 0)

        # T-Edge-Voice-02
        self.assertGreater(_glob_count("voice/good/*barge*_accept.json"), 0)
        self.assertGreater(_glob_count("voice/bad/*barge*_reject.json"), 0)


if __name__ == "__main__":
    unittest.main()

