from __future__ import annotations

from host_brain.app import obs_routes_core as obs


def test_build_trace_subgraphs_from_nodes_raw_reads_subagent_audit_refs() -> None:
    nodes_raw = [
        {
            "node_id": "sub_agent:intent_agent",
            "output_data": {
                "audit_refs": {
                    "subgraph_commit_summary": {
                        "subgraph_id": "intent_agent_subgraph",
                        "node_count": 2,
                        "success_count": 2,
                        "fail_count": 0,
                        "committed_at_ms": 123456,
                        "graph_trace_flat": [
                            {"node_id": "planner_constraints", "status": "OK", "ts_ms": 1},
                            {"node_id": "tool_planner", "status": "OK", "ts_ms": 2},
                        ],
                    }
                }
            },
        }
    ]

    subgraphs = obs._build_trace_subgraphs_from_nodes_raw(nodes_raw)

    assert len(subgraphs) == 1
    subgraph = subgraphs[0]
    assert subgraph["subgraph_id"] == "intent_agent_subgraph"
    assert subgraph["owner_node_id"] == "sub_agent:intent_agent"
    assert subgraph["node_count"] == 2
    assert len(subgraph["nodes"]) == 2
    assert len(subgraph["edges"]) == 1


def test_merge_trace_subgraphs_deduplicates_by_subgraph_id() -> None:
    base = [{"subgraph_id": "intent_agent_subgraph", "nodes": []}]
    extra = [
        {"subgraph_id": "intent_agent_subgraph", "nodes": [1]},
        {"subgraph_id": "code_refine_subgraph", "nodes": [2]},
    ]

    merged = obs._merge_trace_subgraphs(base, extra)

    assert len(merged) == 2
    assert merged[0]["subgraph_id"] == "intent_agent_subgraph"
    assert merged[1]["subgraph_id"] == "code_refine_subgraph"
