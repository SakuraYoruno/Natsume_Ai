from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.channels.mateengine import (  # noqa: E402
    EVENT_MOB_PROXIMITY,
    SUPPORTED_SIGNAL_EVENTS,
    send_mateengine_say,
    send_mateengine_signal,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a MateEngine bridge probe event.")
    parser.add_argument("--say", default=None, help="Send free text through the Natsume MateEngine local bridge.")
    parser.add_argument("--event-type", default=EVENT_MOB_PROXIMITY, choices=sorted(SUPPORTED_SIGNAL_EVENTS))
    parser.add_argument("--name", default="Natsume Bridge")
    parser.add_argument("--event-id", default="natsume_bridge")
    parser.add_argument("--phase", default="enter")
    parser.add_argument("--distance", type=float, default=1.0)
    parser.add_argument("--biome", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--ttl", type=float, default=8.0)
    args = parser.parse_args()

    if args.say:
        result = send_mateengine_say(
            text=args.say,
            ttl=args.ttl,
            host=args.host,
            port=args.port,
        )
    else:
        result = send_mateengine_signal(
            event_type=args.event_type,
            name=args.name,
            event_id=args.event_id,
            phase=args.phase,
            distance=args.distance,
            biome=args.biome,
            host=args.host,
            port=args.port,
        )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
