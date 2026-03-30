from host_brain.retrieval.query_planner import QueryPlanner


def test_first_turn_greeting_still_skips_retrieval() -> None:
    planner = QueryPlanner()

    plan = planner.plan("hello", turn_index=0)

    assert plan.skip_retrieval is True
    assert plan.target_stores == []


def test_non_first_turn_phatic_uses_minimal_context_route() -> None:
    planner = QueryPlanner()

    plan = planner.plan("hello", turn_index=4)

    assert plan.skip_retrieval is False
    assert plan.intent == "phatic"
    assert plan.target_stores == ["episodic_vector"]


def test_affection_messages_route_to_bond_and_memory_without_style_route() -> None:
    planner = QueryPlanner()

    plan = planner.plan("我爱你", turn_index=2)

    assert plan.skip_retrieval is False
    assert plan.intent == "affection"
    assert plan.target_stores == ["neo4j_bond", "episodic_vector"]
    assert plan.max_rounds == 1
