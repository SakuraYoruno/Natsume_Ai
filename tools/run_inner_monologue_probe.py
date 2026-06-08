from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.inner_monologue import (
    build_inner_monologue_digest,
    build_private_inner_thought_event,
)
from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state
from host_brain.persona.surface_influence_packet import build_surface_influence_packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe private inner monologue compression.")
    parser.add_argument("--out-dir", default="", help="Artifact directory. Defaults to artifacts/inner_monologue_probe/<ts>.")
    parser.add_argument("--user-id", default="user_1")
    parser.add_argument("--topic", default="主链验证")
    args = parser.parse_args()

    ts = int(time.time() * 1000)
    out_dir = Path(args.out_dir) if args.out_dir else Path("artifacts") / "inner_monologue_probe" / str(ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    private_note = f"我还记着{args.topic}这件事，等他回来要先把最要紧的一步说清楚。"
    inner_state: dict[str, Any] = {
        "user_id": args.user_id,
        "loneliness": 0.42,
        "anticipation": 0.68,
        "continuity_score": 0.74,
        "anticipation_topics": [args.topic],
    }
    thought = build_private_inner_thought_event(
        note=private_note,
        focus="topic",
        source="probe",
        trigger="manual_probe",
        now_ms=ts,
        state=inner_state,
        offline_hours=2.0,
    )
    inner_state["inner_stream"] = [thought.as_dict()]
    digest = build_inner_monologue_digest(
        inner_state=inner_state,
        offline_state={
            "absence_band": "short",
            "offline_hours": 2.0,
            "loneliness_level": 0.42,
            "anticipation_level": 0.68,
            "continuity_score": 0.74,
        },
    ).as_surface_state()
    self_directive = build_self_directive_packet(load_persistent_self_state(user_id=args.user_id))
    packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_context={
            "default_mode_state": {
                **digest,
                "self_directive": self_directive,
                "last_note": private_note,
            }
        },
    ).as_dict()
    packet_text = json.dumps(packet, ensure_ascii=False)
    leak_detected = private_note in packet_text

    (out_dir / "private_thought_event.json").write_text(
        json.dumps(thought.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "surface_digest.json").write_text(
        json.dumps(digest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "self_directive.json").write_text(
        json.dumps(self_directive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "surface_influence_packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = [
        "# Inner Monologue Probe",
        "",
        f"- user_id: `{args.user_id}`",
        f"- topic: `{args.topic}`",
        f"- private_thought_saved: `{bool(thought.private_text)}`",
        f"- surface_packet_private_text_leak: `{leak_detected}`",
        f"- dominant_focus: `{digest.get('dominant_focus')}`",
        f"- emotion_tag: `{digest.get('emotion_tag')}`",
        f"- care_drive: `{digest.get('care_drive')}`",
        f"- continuity_drive: `{digest.get('continuity_drive')}`",
        f"- self_identity: `{self_directive.get('self_identity')}`",
        f"- agency_score: `{self_directive.get('agency_score')}`",
        f"- style_anchor: `{'; '.join(digest.get('style_anchor') or [])}`",
    ]
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OK", "out_dir": str(out_dir), "leak_detected": leak_detected}, ensure_ascii=False))
    return 1 if leak_detected else 0


if __name__ == "__main__":
    raise SystemExit(main())
