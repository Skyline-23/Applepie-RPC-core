import asyncio
import logging
import time
import os
import sys

# Ensure stdout/stderr support UTF-8 for print statements in embedded Python
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from urllib.parse import quote
from pypresence import AioPresence
from pypresence.types import ActivityType

# Default asset key for missing artwork
LARGE_IMAGE = "appicon"

# Enable debug-level logging for this module
logging.basicConfig(level=logging.DEBUG)


# ─── Monkey‑patch AioPresence to add async set_activity (like sync Client) ──
import uuid


async def _aio_set_activity(
    self,
    *,
    pid: int | None = None,
    activity_type: ActivityType | int = ActivityType.PLAYING,
    **kwargs,
):
    """
    Async equivalent of pypresence.Client.set_activity.
    Build a Discord RPC activity payload from kwargs and send it via send_data.
    Usage:
        await rpc.set_activity(details="Title", state="Artist", large_image="appicon", activity_type=ActivityType.LISTENING)
    """
    if pid is None:
        pid = os.getpid()

    # Build the 'activity' dict exactly as Discord expects
    # Enums vs int
    atype = activity_type.value if hasattr(activity_type, "value") else activity_type
    activity: dict = {"type": int(atype)}
    activity.update({k: v for k, v in kwargs.items() if v is not None})
    print(
        f"[discord_service] _aio_set_activity payload: {{pid: {pid}, kwargs: {kwargs}}}"
    )
    payload = {
        "cmd": "SET_ACTIVITY",
        "args": {"pid": pid, "activity": activity},
        "nonce": str(uuid.uuid4()),
    }
    # send_data is sync in some versions, async in others
    sender = self.send_data
    if asyncio.iscoroutinefunction(sender):
        await sender(1, payload)
    else:
        sender(1, payload)


# Attach the method to AioPresence
AioPresence.set_activity = _aio_set_activity  # attach as normal function (Python 3)


def make_activity(meta: dict, source: str, country: str) -> dict:
    now = int(time.time())
    pos = meta.get("position") or 0
    dur = meta.get("duration") or 0
    title = meta.get("title") or f"▶︎ {source}"
    state = meta.get("artist") or source
    album = meta.get("album")

    assets = {
        "large_image": meta.get("artworkUrl") or LARGE_IMAGE,
        # Show album name under the image hover text
        **(
            {"large_text": (meta.get("album") or "")[:128]} if meta.get("album") else {}
        ),
    }

    buttons: list = []
    if itunes_url := meta.get("itunes_url"):
        buttons.append(
            {
                "label": "Play on Apple Music",
                "url": itunes_url,
            }
        )
    elif title:
        query = quote(f"{title} {album}")
        buttons.append(
            {
                "label": "Search on Apple Music",
                "url": f"https://music.apple.com/{country}/search?term={query}",
            }
        )

    activity = {
        "details": title[:128],
        "state": state[:128],
        "assets": assets,
        "buttons": buttons,
        "instance": False,
    }
    if dur > 0:
        activity["timestamps"] = {"start": now - int(pos), "end": now + int(dur - pos)}
    print(f"[discord_service] make_activity output: {activity}")
    return activity


async def init_rpc(client_id: str) -> AioPresence:
    rpc = AioPresence(client_id)
    await rpc.connect()
    return rpc


def init_rpc_sync(client_id: str) -> AioPresence:
    """
    Synchronous wrapper around init_rpc to return a connected AioPresence.
    """
    return asyncio.run(init_rpc(client_id))


async def _set_activity_async(rpc: AioPresence, **kwargs) -> any:
    logging.debug(f"Setting activity with kwargs: {kwargs}")
    await rpc.set_activity(**kwargs, activity_type=ActivityType.LISTENING)


def set_activity(
    rpc: AioPresence,
    meta: dict,
    source: str,
    country: str,
) -> None:
    print(
        f"[discord_service] set_activity called with meta={meta}, source={source}, country={country}"
    )
    activity = make_activity(meta, source, country)
    logging.debug(f"Scheduling activity with: activity={activity}")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_set_activity_async(rpc, **activity))
    except RuntimeError:
        asyncio.run(_set_activity_async(rpc, **activity))


async def clear_activity(rpc: AioPresence):
    await rpc.clear()
