def bypass_stage_engine(engine, state):
    engine.apply_event(
        user_id="user",
        update_kind="INTERACTION",
        evidence_refs=["event://bypass"],
    )
    state.total_score = 999
    state.current_stage = "SOULMATE"
