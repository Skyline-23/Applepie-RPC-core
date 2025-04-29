import asyncio
import threading
import logging
from pyatv import scan, connect, pair
from pyatv.const import Protocol
from pyatv.interface import Playing
from pyatv.storage.file_storage import FileStorage

# Create a persistent event loop in a background thread
_PAIR_LOOP = asyncio.new_event_loop()
_PAIR_THREAD = threading.Thread(target=_PAIR_LOOP.run_forever, daemon=True)
_PAIR_THREAD.start()

# Use the same storage as atvremote (i.e. ~/.pyatv.conf)
STORAGE = FileStorage.default_storage(_PAIR_LOOP)


# Enable debug-level logging for this module
logging.basicConfig(level=logging.DEBUG)


async def atv_props(ip: str, credentials: str | None = None) -> dict | None:
    """
    Use pyatv metadata service to fetch now-playing info from Apple TV.
    """
    logging.debug(f"Scanning for Apple TV at {ip}...")
    # Perform an asynchronous scan restricted to the given IP
    await STORAGE.load()
    services = await scan(
        loop=_PAIR_LOOP, hosts=[ip], protocol=Protocol.AirPlay, storage=STORAGE
    )
    if not services:
        logging.debug(f"No Apple TV found at {ip}")
        return None
    let_config = services[0]
    if credentials:
        let_config.credentials = credentials
    client = await connect(
        let_config, loop=_PAIR_LOOP, protocol=Protocol.AirPlay, storage=STORAGE
    )
    # If no playback info (idle), return None early
    metadata = client.metadata
    playing_info = await metadata.playing()
    if playing_info.device_state.name == "Idle":
        logging.debug("No playback information available (device idle)")
        client.close()
        return None
    try:
        # Fetch metadata
        playing: Playing = playing_info
        logging.debug(f"[pyatv_service] Playing: {playing}")
        isPlaying = playing.device_state
        if isPlaying.name == "Paused":
            logging.debug("No playback information available (device paused)")
            return None
        return {
            "title": playing.title,
            "artist": playing.artist,
            "album": playing.album,
            "duration": playing.total_time,  # in seconds
            "position": playing.position,  # in seconds
            "state": playing.device_state.name.lower(),
            "itunes_id": playing.itunes_store_identifier,
        }
    except Exception as e:
        print(f"Error fetching metadata: {e}")
        return None
    finally:
        client.close()


def get_atv_props_sync(ip: str, credentials: str | None = None) -> dict | None:
    """
    Synchronous wrapper around atv_props.
    """
    future = asyncio.run_coroutine_threadsafe(
        atv_props(ip=ip, credentials=credentials), _PAIR_LOOP
    )
    return future.result()


# --- Pairing helpers ---

# Store ongoing pairing sessions
PAIRINGS: dict[str, any] = {}


# --- Async pairing session helpers ---
async def pair_device_begin(host: str) -> bool:
    """
    Start pairing with Apple TV at given host. Returns True if initiation succeeded.
    """
    protocol = Protocol.AirPlay
    # Strip port if included
    timeout = 5
    loop = _PAIR_LOOP
    devices = await scan(loop=loop, hosts=[host], protocol=protocol, storage=STORAGE)
    # Compare host string to device address
    target = next((d for d in devices if str(d.address) == host), None)
    if not target:
        logging.error(f"No device found at {host}")
        return False
    pairing = await pair(target, protocol, loop, storage=STORAGE)
    await pairing.begin()
    PAIRINGS[host] = pairing
    return True


async def pair_device_finish(host: str, pin: int) -> str | None:
    """
    Finish pairing by providing PIN. Returns credentials string if succeeded.
    """
    pairing = PAIRINGS.pop(host, None)
    if not pairing:
        logging.error(f"No pairing session for host {host}")
        return None
    pairing.pin(pin)
    await pairing.finish()
    success = pairing.has_paired
    await pairing.close()
    if success:
        # Persist new pairing credentials to disk
        await STORAGE.save()
        return pairing.service.credentials
    return None


def pair_device_begin_sync(host: str) -> bool:
    """
    Sync wrapper for pair_device_begin using a shared event loop.
    """
    future = asyncio.run_coroutine_threadsafe(pair_device_begin(host), _PAIR_LOOP)
    return future.result()


def pair_device_finish_sync(host: str, pin: int) -> str | None:
    """
    Sync wrapper for pair_device_finish using a shared event loop.
    """
    future = asyncio.run_coroutine_threadsafe(pair_device_finish(host, pin), _PAIR_LOOP)
    return future.result()
