#!/usr/bin/env python3
"""
applepie_rpc.py — Apple Music · HomePod · Apple TV Discord RPC
--------------------------------------------------------------
• Discord Rich Presence: pypresence (>= 4.3.2 dev / PR #244)
• HomePod:  AirPlay /playback‑info  (XML plist)
• Apple TV: atvscript (mrp)  ← must be paired once
• mDNS:     zeroconf‑asyncio
"""

from __future__ import annotations

import asyncio
import json
import shelve


import subprocess
import os
from appdirs import user_cache_dir  # pip install appdirs

# Persistent cache for track extras
appname = "applepie_rpc"
appauthor = "Skyline23"
cache_dir = user_cache_dir(appname, appauthor)
os.makedirs(cache_dir, exist_ok=True)
cache_path = os.path.join(cache_dir, "applepie_cache")
cache = shelve.open(cache_path)
import re
import time
from typing import Dict, Optional
from urllib.parse import quote

import aiohttp
from aiohttp import ClientSession
from pypresence import AioPresence
from pypresence.types import ActivityType  # 🎧 icon
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
import signal
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="[%(asctime)s] %(levelname)s:%(message)s"
)

paused = False  # global toggle for pausing playback updates


async def fetch_track_extras(props: dict) -> dict:
    """
    Lookup artworkUrl (512x512) and iTunesUrl via iTunes Lookup API using track ID.
    """
    # Try cached result first (ensure key is a string)
    raw_key = (
        props.get("itunes_id")
        or f"{props.get('name')}|{props.get('artist')}|{props.get('album')}"
    )
    key = str(raw_key)
    if key in cache:
        return cache[key]

    # If we have a direct iTunes track ID, use the lookup API for artwork
    if props.get("itunes_id"):
        lookup_url = f"https://itunes.apple.com/lookup?id={props['itunes_id']}&country={get_country_code()}"
        try:
            async with session.get(lookup_url) as resp:
                if resp.status != 200:
                    return {}
                text = await resp.text()
                data = json.loads(text)
        except Exception:
            return {}
        results = data.get("results", [])
        if results:
            first = results[0]
            art = first.get("artworkUrl100", "").replace("100x100", "512x512")
            result = {"artworkUrl": art, "iTunesUrl": first.get("trackViewUrl")}
            cache[key] = result
            cache.sync()
            return result
    # No track ID or no results
    cache[key] = {}
    cache.sync()
    return {}


def get_country_code() -> str:
    """Return ISO country code from macOS AppleLocale or LANG, default to 'us'."""
    # 1) macOS 시스템 전역 로케일 읽기
    try:
        res = subprocess.run(
            ["defaults", "read", "NSGlobalDomain", "AppleLocale"],
            capture_output=True,
            text=True,
            check=True,
        )
        loc = res.stdout.strip()
        if "_" in loc:
            return loc.split("_")[1].lower()
    except Exception:
        pass

    # 2) LANG 환경변수 사용 fallback
    lang = os.getenv("LANG", "")
    if "_" in lang:
        return lang.split("_")[1].lower()

    return "us"


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


# Signal handlers for graceful shutdown
def handle_signal(signum, frame):
    logging.info(f"Received signal {signum}, shutting down...")
    asyncio.get_event_loop().create_task(cleanup_and_exit())


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ──────────────────────────────── CONFIG ──────────────────────────────────
CLIENT_ID = os.getenv("APPLEPIE_RPC_CLIENT_ID", "1362417259154374696")
UPDATE_INTERVAL = 1  # seconds
SMALL_IMAGE = "appicon"  # must exist in your Discord application

# ───────────────────────── GLOBAL STATE ───────────────────────────────────
session: ClientSession
rpc: AioPresence
homepods: Dict[str, tuple[str, int]] = {}
appletvs: Dict[str, tuple[str, int]] = {}


# ─────────────────────────── mDNS SCAN ────────────────────────────────────
class AirPlayListener:
    def __init__(self, zc: AsyncZeroconf):
        self._zc = zc

    def remove_service(self, zc, t, name):
        homepods.pop(name, None)
        appletvs.pop(name, None)

    async def _extract(self, coro, name):
        info = await coro
        if not info or not info.addresses:
            return
        host = ".".join(map(str, info.addresses[0]))
        port = info.port
        if "HomePod" in name:
            homepods[name] = (host, port)
        elif "Apple TV" in name:
            appletvs[name] = (host, port)

    def add_service(self, zc, t, name):
        coro = self._zc.async_get_service_info(t, name)
        asyncio.create_task(self._extract(coro, name))

    # zeroconf ≥ 0.131
    def update_service(self, zc, t, name):
        self.remove_service(zc, t, name)
        self.add_service(zc, t, name)


async def mdns_browse():
    zc = AsyncZeroconf()
    AsyncServiceBrowser(zc.zeroconf, "_airplay._tcp.local.", AirPlayListener(zc))


# ──────────────────────── METADATA HELPERS ────────────────────────────────
def _xml_val(xml: str, key: str, cast=str):
    """
    Extract plist value for <key>foo</key>.
    Supports <string>, <real>, <integer>.
    """
    m = re.search(
        rf"<key>\s*{key}\s*</key>\s*<(?:string|real|integer)>([^<]+)",
        xml,
        re.I,
    )
    return cast(m.group(1)) if m else None


async def homepod_props(host: str, port: int) -> Optional[dict]:
    url = f"http://{host}:{port}/playback-info"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            xml = await resp.text()
    except Exception:
        return None

    rate = _xml_val(xml, "rate", float) or 0
    return {
        "title": _xml_val(xml, "title"),
        "artist": _xml_val(xml, "artist"),
        "album": _xml_val(xml, "album"),
        "duration": _xml_val(xml, "duration", float),
        "position": _xml_val(xml, "position", float),
        "state": "playing" if rate > 0 else "paused",
    }


async def mac_now_playing() -> Optional[dict]:
    """
    Try both Music and iTunes apps via AppleScript to determine playback metadata.
    """
    for app in ("Music", "iTunes"):
        osa = (
            f'tell application "{app}"\n'
            "  if player state is playing then\n"
            '    return (name of current track) & "||" & (artist of current track) & "||" & '
            '(album of current track) & "||" & (duration of current track) & "||" & '
            "(player position as text)\n"
            "  end if\n"
            "end tell"
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            osa,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        text = out.decode().strip() if out else ""
        # print(f"[mac_now_playing] app={app}, output={text}")
        if not text:
            continue
        try:
            name, artist, album, dur, pos = text.split("||")
            return {
                "title": name,
                "artist": artist,
                "album": album,
                "duration": float(dur),
                "position": float(pos),
                "state": "playing",
            }
        except Exception as e:
            print(f"[mac_now_playing] parse error for {app}: {e}")
            continue
    return None


async def atv_props(host: str) -> Optional[dict]:
    proc = await asyncio.create_subprocess_exec(
        "atvscript",
        "-s",
        host,
        "playing",
        "--output",
        "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not out:
        return None
    data = json.loads(out)
    if data.get("result") != "success":
        return None
    return {
        "title": data.get("title"),
        "artist": data.get("artist"),
        "album": data.get("album"),
        "duration": data.get("total_time"),
        "position": data.get("position"),
        "state": data.get("device_state", "").lower(),
        "itunes_id": data.get("itunes_store_identifier"),
    }


# ───────────────────────── ACTIVITY BUILDER ───────────────────────────────
def make_activity(meta: dict, source: str) -> dict:
    # print(meta)
    now = int(time.time())
    pos = meta.get("position") or 0
    dur = meta.get("duration") or 0
    details = meta.get("title") or f"▶︎ {source}"
    state = meta.get("artist") or source
    album = meta.get("album")

    country = get_country_code()

    assets = {
        "large_image": meta.get("artworkUrl")
        or SMALL_IMAGE,  # use album artwork URL or fallback asset
        "small_image": SMALL_IMAGE,  # show default small icon
        "small_text": "Apple Music",  # label for small icon
        **({"large_text": album[:128]} if album else {}),
    }

    # --- Apple Music button ---------------------------------
    buttons: list = []
    if itunes_id := meta.get("itunes_id"):
        buttons.append(
            {
                "label": "Play on Apple Music",
                "url": f"https://music.apple.com/{country}/song/{itunes_id}",
            }
        )
    elif album:
        query = quote(f"{details} {state}")
        buttons.append(
            {
                "label": "Search on Apple Music",
                "url": f"https://music.apple.com/{country}/search?term={query}",
            }
        )

    activity: dict = {
        "details": details[:128],
        "state": state[:128],
        "assets": assets,
        "buttons": buttons,
        "instance": False,  # required for buttons to show
    }
    if dur > 0:
        activity["timestamps"] = {"start": now - int(pos), "end": now + int(dur - pos)}
    return activity


async def cleanup_and_exit():
    logging.info("Cleaning up before exit...")
    try:
        await rpc.clear()
    except Exception:
        pass
    cache.close()
    await session.close()
    sys.exit(0)


async def monitor_commands(cmd_file="/tmp/applepie_rpc_cmd"):
    global paused, UPDATE_INTERVAL
    while True:
        try:
            if os.path.exists(cmd_file):
                with open(cmd_file, "r+") as f:
                    for line in f:
                        line = line.strip()
                        if line.upper() == "PAUSE":
                            paused = True
                            logging.info("Paused by command")
                        elif line.upper() == "RESUME":
                            paused = False
                            logging.info("Resumed by command")
                        elif line.upper().startswith("INTERVAL:"):
                            try:
                                val = int(line.split(":", 1)[1])
                                UPDATE_INTERVAL = val
                                logging.info(f"Interval set to {val} by command")
                            except Exception:
                                logging.error(f"Invalid interval command: {line}")
                    f.truncate(0)
        except Exception as e:
            logging.error(f"Error monitoring commands: {e}")
        await asyncio.sleep(1)


# ────────────────────────── MAIN UPDATE LOOP ──────────────────────────────
async def update_loop():
    await rpc.connect()
    # print("[update_loop] RPC connected")
    while True:
        # If paused, skip updating presence
        if paused:
            # Clear presence when paused
            await rpc.clear()
            await asyncio.sleep(UPDATE_INTERVAL)
            continue

        # print(f"[update_loop] homepods={homepods}, appletvs={appletvs}")
        meta: Optional[dict] = None
        source = ""

        # 1) Check local Music.app if playing
        mac_meta = await mac_now_playing()
        if mac_meta and mac_meta.get("state") == "playing":
            meta = mac_meta
            source = "Music.app"
        else:
            # 2) Try HomePod
            for host, port in list(homepods.values()):
                pod_meta = await homepod_props(host, port)
                if pod_meta:
                    meta = pod_meta
                    source = "HomePod"
                    break
            # 3) If no HomePod, try Apple TV
            if not meta:
                for host, _ in list(appletvs.values()):
                    tv_meta = await atv_props(host)
                    if tv_meta:
                        meta = tv_meta
                        source = "Apple TV"
                        break

        if meta:
            # If the track is not playing, clear activity and skip
            if meta.get("state") != "playing":
                # print(f"[update_loop] state={meta.get('state')} → clearing activity")
                await rpc.clear()
                await asyncio.sleep(UPDATE_INTERVAL)
                continue

            # Fetch artwork and iTunes URL for any source
            extras = await fetch_track_extras(
                {
                    "name": meta.get("title", ""),
                    "artist": meta.get("artist", ""),
                    "album": meta.get("album") or "",
                    "itunes_id": meta.get("itunes_id"),
                }
            )
            # print(f"[update_loop] fetched extras={extras}")
            meta.update(extras)

            # print(f"[debug] chosen source={source} meta={meta}")
            activity = make_activity(meta, source)
            # print(f"[update_loop] Sending activity: {activity}")
            try:
                await rpc.set_activity(
                    **activity,
                    activity_type=ActivityType.LISTENING,
                )
            except Exception as err:
                print("rpc.set_activity error:", err)
        else:
            # print("[update_loop] No meta, clearing activity")
            await rpc.clear()

        await asyncio.sleep(UPDATE_INTERVAL)


# ────────────────────────────── ENTRY ─────────────────────────────────────
async def main():
    global session, rpc
    session = aiohttp.ClientSession()
    rpc = AioPresence(CLIENT_ID)
    try:
        await mdns_browse()
        # Start monitoring external commands (pause/resume/interval)
        asyncio.create_task(monitor_commands())
        await update_loop()
    finally:
        cache.close()
        await session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
