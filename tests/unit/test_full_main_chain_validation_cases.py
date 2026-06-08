import json

from tools.full_main_chain_validation_cases import CASES, _case_payload


def test_validation_payload_keeps_review_focus_out_of_runtime_control() -> None:
    case = next(item for item in CASES if item.case_id == "02_presence")

    payload = _case_payload(case, person_id="test_user", session_id="test_session")
    control = payload["surface_task_control"]
    rendered = json.dumps(payload, ensure_ascii=False)

    assert control["response_move"] == "presence_ack"
    assert control["facts"] == []
    assert "接住用户找人的意图" not in rendered
    assert "人工评审重点" not in rendered
