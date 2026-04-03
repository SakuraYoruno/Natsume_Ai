import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import edge_client.__main__ as edge_main


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "edge" / "start"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestEdgeBootstrapContract(unittest.TestCase):
    def test_t_edge_start_01_main_starts_with_configured_bind(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "start_env_accept.json")
        with patch.dict(os.environ, fixture, clear=False):
            with patch("edge_client.__main__.uvicorn.run") as mock_run:
                exit_code = edge_main.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(2, mock_run.call_count)
        observed = {
            (call.kwargs["host"], call.kwargs["port"], call.kwargs["log_level"])
            for call in mock_run.call_args_list
        }
        self.assertIn(("0.0.0.0", 19000, "info"), observed)
        self.assertIn(("127.0.0.1", 18888, "info"), observed)

    def test_t_edge_start_01_invalid_config_returns_nonzero(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "invalid_env_reject.json")
        with patch.dict(os.environ, fixture, clear=False):
            with patch("edge_client.__main__.uvicorn.run") as mock_run:
                exit_code = edge_main.main()

        self.assertEqual(2, exit_code)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
