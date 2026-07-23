from __future__ import annotations

from host_brain.executive_runtime.skill_dag import build_skill_dag_plan, merge_plan_steps_with_dag


def test_build_skill_dag_plan_creates_parallel_cluster_and_barrier() -> None:
    steps = [
        {"step_index": 1, "route": "skill", "target_id": "web_search", "description": "search", "task_description": "search", "allow_parallel": True},
        {"step_index": 2, "route": "skill", "target_id": "system_info", "description": "system", "task_description": "system", "allow_parallel": True},
        {"step_index": 3, "route": "skill", "target_id": "memory_store", "description": "remember", "task_description": "remember", "allow_parallel": False},
        {"step_index": 4, "route": "delegate", "target_id": "intent_agent", "description": "summarize", "task_description": "summarize", "allow_parallel": True},
    ]

    dag = build_skill_dag_plan(plan_id="plan::cluster", steps=steps, parallel_window=4)

    assert len(dag.nodes) == 4
    n1, n2, n3, n4 = dag.nodes
    assert n1.dependencies == ()
    assert n2.dependencies == ()
    assert set(n3.dependencies) == {n1.node_id, n2.node_id}
    assert n4.dependencies == (n3.node_id,)
    assert dag.execution_batches == [[n1.node_id, n2.node_id], [n3.node_id], [n4.node_id]]


def test_build_skill_dag_plan_respects_explicit_dependencies_and_retry_policy() -> None:
    steps = [
        {
            "node_id": "collect",
            "step_index": 1,
            "route": "skill",
            "target_id": "web_search",
            "description": "collect",
            "task_description": "collect",
            "allow_parallel": True,
            "fallback_policy": "retry_once",
        },
        {
            "node_id": "summarize",
            "step_index": 2,
            "route": "delegate",
            "target_id": "intent_agent",
            "description": "summarize",
            "task_description": "summarize",
            "allow_parallel": True,
            "depends_on": ["collect"],
            "retry_limit": 2,
            "retry_backoff_ms": 350,
            "idempotency_key": "idem::fixed",
        },
    ]

    dag = build_skill_dag_plan(plan_id="plan::explicit", steps=steps, parallel_window=4)
    n1, n2 = dag.nodes

    assert n1.node_id == "collect"
    assert n1.retry_limit == 1
    assert n1.retry_backoff_ms > 0
    assert n1.idempotency_key.startswith("idem::")
    assert n2.dependencies == ("collect",)
    assert n2.retry_limit == 2
    assert n2.retry_backoff_ms == 350
    assert n2.idempotency_key == "idem::fixed"


def test_build_skill_dag_plan_honors_parallel_window_and_merge_metadata() -> None:
    steps = [
        {"step_index": 1, "route": "skill", "target_id": "a", "description": "a", "task_description": "a", "allow_parallel": True},
        {"step_index": 2, "route": "skill", "target_id": "b", "description": "b", "task_description": "b", "allow_parallel": True},
        {"step_index": 3, "route": "skill", "target_id": "c", "description": "c", "task_description": "c", "allow_parallel": True},
    ]

    dag = build_skill_dag_plan(plan_id="plan::window", steps=steps, parallel_window=1)
    merged_steps = merge_plan_steps_with_dag(steps=steps, dag_plan=dag)

    assert len(dag.execution_batches) == 3
    assert all(len(batch) == 1 for batch in dag.execution_batches)
    assert len(merged_steps) == 3
    assert merged_steps[0]["node_id"]
    assert "dependencies" in merged_steps[0]
    assert "retry_limit" in merged_steps[0]


def test_build_skill_dag_plan_preserves_step_args_payload() -> None:
    steps = [
        {
            "step_index": 1,
            "route": "skill",
            "target_id": "agent_worker",
            "description": "fix test",
            "task_description": "fix failing test",
            "allow_parallel": True,
            "args": {
                "workspace_strategy": "runtime_repo",
                "task": "fix failing test",
            },
        },
        {
            "step_index": 2,
            "route": "skill",
            "target_id": "session_summary",
            "description": "summarize",
            "task_description": "summarize current session",
            "allow_parallel": True,
        },
    ]

    dag = build_skill_dag_plan(plan_id="plan::args", steps=steps, parallel_window=2)
    merged_steps = merge_plan_steps_with_dag(steps=steps, dag_plan=dag)

    assert dag.nodes[0].args_payload["workspace_strategy"] == "runtime_repo"
    assert dag.nodes[0].args_payload["task"] == "fix failing test"
    assert merged_steps[0]["args_payload"]["workspace_strategy"] == "runtime_repo"


def test_cycle_is_non_executable_instead_of_being_forced_serial() -> None:
    dag = build_skill_dag_plan(
        plan_id="plan::cycle",
        steps=[
            {
                "node_id": "left",
                "step_index": 1,
                "route": "skill",
                "target_id": "web_search",
                "description": "left",
                "task_description": "left",
                "depends_on": ["right"],
            },
            {
                "node_id": "right",
                "step_index": 2,
                "route": "skill",
                "target_id": "system_info",
                "description": "right",
                "task_description": "right",
                "depends_on": ["left"],
            },
        ],
    )

    assert dag.has_cycle_warning is True
    assert dag.execution_batches == []
    assert dag.as_dict()["execution_batches"] == []
