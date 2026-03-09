import json

from host_brain.core.errors import CONFIG_REQUIRED_MISSING
from host_brain.retrieval.recall_fusion import load_recall_runtime_config


def test_recall_route_requires_store_id() -> None:
    env = {
        "RETR_RECALL_ENABLED": "true",
        "RETR_RECALL_DEFAULT_ROUTE": "episodic_route",
        "RETR_RECALL_ROUTES_JSON": json.dumps(
            {
                "episodic_route": {
                    "collection": "episodic",
                    "top_k": 4,
                    "score_weight": 1.0,
                    "node_selector": "host-01",
                },
            },
            ensure_ascii=False,
        ),
    }
    result = load_recall_runtime_config(env=env)
    assert not result.success
    assert result.reason_code == CONFIG_REQUIRED_MISSING
    assert any(
        issue.field == "RETR_RECALL_ROUTES_JSON.episodic_route.store_id"
        for issue in result.issues
    )
