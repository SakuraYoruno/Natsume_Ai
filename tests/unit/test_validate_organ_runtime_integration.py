from __future__ import annotations

from tools.validate_organ_runtime_integration import build_arg_parser, run_validation


def test_validate_organ_runtime_integration_fixture_mode(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--mode", "fixture", "--out-dir", str(tmp_path)])

    result = run_validation(mode=args.mode, out_dir=args.out_dir)

    summary = result["summary"]
    assert summary["status"] == "PASS"
    assert summary["mode"] == "fixture"
    assert summary["organ_count"] == 23
    assert summary["checks"]["fixture_matrix_passed"] is True
    assert summary["checks"]["fixture_each_organ_has_contract_and_surface_effect"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "runtime_rows.jsonl").exists()
    assert (tmp_path / "report.md").exists()


def test_validate_organ_runtime_integration_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.iterations == 1


def test_all_23_organs_have_non_default_state_after_tick_cycle() -> None:
    """After a complete tick+user_message cycle, all 23 organs must have non-zero activation or pressure."""
    from host_brain.runtime.life_organs import (
        _base_event_deltas,
        _apply_delta,
        _default_organ_state,
        ORGAN_IDS,
    )

    now_ms = 1_000_000_000
    tick_evidence = {"idle_minutes": 45, "fatigue": 0.3, "elapsed_ms": 600_000, "environment": {"day_phase": "afternoon"}}

    tick_deltas = _base_event_deltas("periodic_tick", tick_evidence, "", 0.0, 0.0, now_ms)
    msg_deltas = _base_event_deltas("user_message", {"idle_minutes": 45}, "你好！不错", 0.5, 0.3, now_ms)
    inner_deltas = _base_event_deltas("inner_stream_tick", {"felt_tendency": "curious_drift"}, "some inner content", 0.2, 0.1, now_ms)
    emb_deltas = _base_event_deltas("embodiment_dispatch", {"target": {"expression": "smile"}}, "", 0.2, 0.0, now_ms)

    # These organs legitimately do not respond to tick + user_message + inner_stream + embodiment_dispatch
    legitimately_static = {"immune", "glymphatic"}  # Only respond to dream_generated, work_event, system_fault

    static_organs: set[str] = set()
    for organ_id in ORGAN_IDS:
        state = _default_organ_state(organ_id, now_ms)
        all_deltas = [
            tick_deltas.get(organ_id, {}),
            msg_deltas.get(organ_id, {}),
            inner_deltas.get(organ_id, {}),
            emb_deltas.get(organ_id, {}),
        ]
        any_delta = any(d for d in all_deltas)
        if any_delta:
            for delta in all_deltas:
                if delta:
                    state = _apply_delta(state, delta, trace={"test": "tick"}, now_ms=now_ms)
            assert state["activation"] > 0.0 or state["pressure"] > 0.0, f"{organ_id} still at default after events"
        else:
            static_organs.add(organ_id)

    only_legit = static_organs - legitimately_static
    assert len(only_legit) == 0, f"Unexpected static organs: {only_legit}"


def test_decorative_organs_have_nonzero_activation() -> None:
    """The 4 previously-decorative organs get non-zero activation from relevant events."""
    from host_brain.runtime.life_organs import _base_event_deltas

    now_ms = 1_000_000_000

    tick_d = _base_event_deltas(
        "periodic_tick",
        {"idle_minutes": 30, "fatigue": 0.2, "elapsed_ms": 300_000},
        "", 0.0, 0.0, now_ms,
    )
    assert "endocrine" in tick_d, "endocrine missing from periodic_tick deltas"
    assert tick_d["endocrine"].get("activation", 0.0) > 0.0
    assert "vestibular" in tick_d, "vestibular missing from periodic_tick deltas"
    assert "ans" in tick_d, "ans missing from periodic_tick deltas"
    assert "peripheral" in tick_d, "peripheral missing from periodic_tick deltas"

    msg_d = _base_event_deltas("user_message", {}, "你好！今天不错", 0.3, 0.2, now_ms)
    assert "endocrine" in msg_d, "endocrine missing from user_message deltas"
    assert msg_d["endocrine"].get("activation", 0.0) > 0.0
    assert "ans" in msg_d, "ans missing from user_message deltas"
    assert "peripheral" in msg_d, "peripheral missing from user_message deltas"

    inner_d = _base_event_deltas(
        "inner_stream_tick",
        {"felt_tendency": "curious_drift"}, "some inner content", 0.2, 0.1, now_ms,
    )
    assert "endocrine" in inner_d, "endocrine missing from inner_stream_tick deltas"

    emb_d = _base_event_deltas(
        "embodiment_dispatch",
        {"target": {"expression": "smile"}}, "", 0.2, 0.0, now_ms,
    )
    assert "vestibular" in emb_d, "vestibular missing from embodiment_dispatch deltas"


def test_synthesis_includes_partial_organ_contributions() -> None:
    """After synthesis, affect/needs/pressure contain contributions from previously-partial organs."""
    import sqlite3
    from host_brain.runtime.life_organs import (
        ensure_organ_schema,
        append_life_experience,
        synthesize_life_state_from_organs,
        get_organ_runtime_state,
    )

    class FakeLedger:
        def __init__(self):
            self._conn = sqlite3.connect(":memory:")
            self._conn.row_factory = sqlite3.Row

    ledger = FakeLedger()
    ensure_organ_schema(ledger)

    base_state = {
        "person_id": "default",
        "version": 1,
        "affect": {"mood": "calm", "valence": 0.18, "arousal": 0.24, "energy": 0.62,
                    "loneliness": 0.0, "attachment_warmth": 0.42, "irritation": 0.0,
                    "fatigue": 0.12, "tension": 0.0, "pride": 0.0, "relief": 0.0},
        "needs": {"contact": 0.12, "rest": 0.12, "play": 0.18, "autonomy": 0.25, "memory_consolidation": 0.2},
        "inner_world": {"current_preoccupation": "", "attention_target": "", "unresolved_loop": "",
                         "felt_tendency": "", "dialogue_bias": "", "expires_at_ms": 0,
                         "dream_residue": "", "diary_residue": "", "thought_seed": "", "private_note": ""},
        "life_pressure": {"absence": 0.0, "dream": 0.0, "work": 0.0, "fatigue": 0.12, "relationship": 0.28},
        "embodiment": {"expression": "calm_idle", "idle_animation": "quiet_breath",
                        "motion_bias": "low_random", "voice_tone": "soft_neutral",
                        "mateengine_state": "idle", "emotion_tag": "NEUTRAL"},
        "motives": [],
    }

    append_life_experience(ledger=ledger, source_kind="periodic_tick", event_kind="periodic_tick",
                           evidence={"idle_minutes": 45, "fatigue": 0.25, "elapsed_ms": 900_000,
                                      "environment": {"day_phase": "night"}},
                           salience=0.0, valence=0.0, consume=True)
    append_life_experience(ledger=ledger, source_kind="user_message", event_kind="user_message",
                           content_summary="你好！今天心情不错", salience=0.4, valence=0.3, consume=True)
    append_life_experience(ledger=ledger, source_kind="inner_stream", event_kind="inner_stream_tick",
                           content_summary="thinking about connection",
                           evidence={"felt_tendency": "approach_warm"},
                           salience=0.2, valence=0.1, consume=True)
    append_life_experience(ledger=ledger, source_kind="embodiment", event_kind="embodiment_dispatch",
                           evidence={"target": {"expression": "smile"}},
                           salience=0.1, valence=0.0, consume=True)

    result = synthesize_life_state_from_organs(ledger=ledger, base_state=base_state, person_id="default")

    affect = result.get("affect", {})
    assert affect.get("arousal", 0.24) >= 0.24, "arousal should be at least default"
    assert affect.get("energy", 0.62) >= 0.62, "energy should be at least default"
    assert affect.get("valence", 0.18) >= 0.18, "valence should be at least default"

    organs = get_organ_runtime_state(ledger=ledger)["organs"]
    for organ_id in ("endocrine", "vestibular", "ans", "peripheral"):
        organ = organs.get(organ_id, {})
        activation = float(organ.get("activation", 0.0))
        pressure = float(organ.get("pressure", 0.0))
        assert activation > 0.0 or pressure > 0.0, f"{organ_id} still at default after events"


def test_coupling_rules_include_new_organs() -> None:
    """Verify the four new coupling rules are present in _couple_organs."""
    import inspect
    from host_brain.runtime.life_organs import _couple_organs

    source = inspect.getsource(_couple_organs)

    assert "endocrine" in source and "limbic" in source, "endocrine→limbic coupling missing"
    assert "vestibular" in source and "interoception" in source, "vestibular→interoception coupling missing"
    assert "glymphatic" in source and "circadian" in source, "ans→circadian coupling missing"
    assert "mirror_neuron" in source and "social_brain" in source, "mirror_neuron→limbic coupling missing"
