#!/usr/bin/env python3
"""Inspect active LiveKit rooms, participants, and track state."""
import asyncio
import os
import sys
from typing import Optional

from livekit import api


def _enum_name(value: Optional[object]) -> Optional[str]:
    """Return the name for protobuf enums, falling back to raw value."""
    if value is None:
        return None
    return getattr(value, "name", str(value))


async def inspect_room(target_room: Optional[str] = None) -> int:
    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not url or not api_key or not api_secret:
        print("ERROR: Set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET before running.", file=sys.stderr)
        return 1

    http_url = url.replace("wss://", "https://")

    async with api.LiveKitAPI(url=http_url, api_key=api_key, api_secret=api_secret) as lk:
        rooms_resp = await lk.room.list_rooms(api.ListRoomsRequest())
        rooms = rooms_resp.rooms or []
        if not rooms:
            print("No active LiveKit rooms.")
            return 0

        matched = False
        for room in rooms:
            if target_room and room.name != target_room:
                continue

            matched = True
            print(f"Room: {room.name} | participants={room.num_participants} | empty_timeout={room.empty_timeout}")

            parts_resp = await lk.room.list_participants(api.ListParticipantsRequest(room=room.name))
            participants = parts_resp.participants or []
            if not participants:
                print("  (no participants)")
                continue

            for participant in participants:
                tracks = []
                for publication in participant.tracks or []:
                    tracks.append({
                        "sid": publication.sid,
                        "type": _enum_name(publication.type),
                        "source": _enum_name(publication.source),
                        "muted": publication.muted,
                        "subscription": _enum_name(getattr(publication, "subscription_state", None)),
                    })
                print(
                    "  - {identity} | kind={kind} | speaking={speaking} | tracks={tracks}".format(
                        identity=participant.identity,
                        kind=_enum_name(participant.kind),
                        speaking=getattr(participant, "is_speaking", None),
                        tracks=tracks,
                    )
                )

        if target_room and not matched:
            print(f"Room '{target_room}' not found among active rooms.")

    return 0


def main() -> int:
    room_name = sys.argv[1] if len(sys.argv) > 1 else None
    return asyncio.run(inspect_room(room_name))


if __name__ == "__main__":
    sys.exit(main())
