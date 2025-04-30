import asyncio
import threading
import logging
import os
from pyatv import scan, connect, pair
from pyatv.const import Protocol
from pyatv.const import PairingRequirement
from pyatv.interface import Playing
from pyatv.storage.file_storage import FileStorage, StorageModel

# Create a persistent event loop in a background thread
_PAIR_LOOP = asyncio.new_event_loop()
_PAIR_THREAD = threading.Thread(target=_PAIR_LOOP.run_forever, daemon=True)
_PAIR_THREAD.start()

# Use the same storage as atvremote (i.e. ~/.pyatv.conf)
STORAGE = FileStorage.default_storage(_PAIR_LOOP)


# Enable debug-level logging for this module
logging.basicConfig(level=logging.DEBUG)

# Log pyatv library version at startup
try:
    from importlib.metadata import version, PackageNotFoundError

    pyatv_version = version("pyatv")
    logging.info(f"pyatv version: {pyatv_version}")
except PackageNotFoundError:
    logging.error("Failed to retrieve pyatv version: pyatv package not found")
except Exception as e:
    logging.error(f"Failed to retrieve pyatv version: {e}")


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
    # Strip port if included
    # Ensure storage is loaded before scanning
    await STORAGE.load()
    loop = _PAIR_LOOP
    # Discover all protocols to pick the best one
    devices = await scan(loop=loop, hosts=[host], storage=STORAGE)
    if not devices:
        logging.error(f"No device found at {host}")
        return False
    device = next((d for d in devices if str(d.address) == host), None)
    if not device:
        logging.error(f"No device matching host {host}")
        return False
    # Prefer Companion (MRP) protocol if available, else AirPlay
    protocol = Protocol.AirPlay
    # Now restrict scan to chosen protocol for pairing
    devices = await scan(loop=loop, hosts=[host], protocol=protocol, storage=STORAGE)
    target = next((d for d in devices if str(d.address) == host), None)
    if not target:
        logging.error(f"No device found at {host} (protocol {protocol})")
        return False
    pairing = await pair(target, protocol, loop, storage=STORAGE, name="Applepie-RPC")
    try:
        await pairing.begin()
    except Exception as e:
        logging.error(f"Pairing begin failed for {host}: {e}")
        # Ensure session is closed on failure
        await pairing.close()
        return False
    else:
        # Store session for later finish
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
    try:
        pairing.pin(pin)
        await pairing.finish()
        if pairing.has_paired:
            # Persist new credentials
            await STORAGE.save()
            return pairing.service.credentials
    except Exception as e:
        logging.error(f"Pairing finish failed for {host}: {e}")
    finally:
        # Always close session
        await pairing.close()
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


async def cancel_pairing(host: str) -> bool:
    """
    Cancel an ongoing pairing session for the given host.
    Closes and removes the pairing object without completing it.
    """
    pairing = PAIRINGS.pop(host, None)
    if not pairing:
        logging.debug(f"No pairing session to cancel for {host}")
        return False
    try:
        await pairing.close()
        logging.debug(f"Cancelled pairing session for {host}")
        # After cancellation, clear any stored pairing credentials
        await remove_pairing()
        logging.debug(f"Cleared stored pairing credentials after cancel for {host}")
        return True
    except Exception as e:
        logging.error(f"Error cancelling pairing for {host}: {e}")
        return False


def cancel_pairing_sync(host: str) -> bool:
    """
    Synchronous wrapper around cancel_pairing.
    """
    future = asyncio.run_coroutine_threadsafe(cancel_pairing(host), _PAIR_LOOP)
    return future.result()


async def is_pairing_needed(host: str, protocol: Protocol = Protocol.AirPlay) -> bool:
    """
    Check if pairing is mandatory for the given host and protocol.
    Returns True if PairingRequirement is Mandatory, False otherwise.
    """
    logging.debug(f"[pyatv_service] is_pairing_needed called for host={host}")
    # Ensure storage is loaded before scanning
    await STORAGE.load()
    # Scan all protocols to detect device services
    devices = await scan(loop=_PAIR_LOOP, hosts=[host], storage=STORAGE)
    if not devices:
        logging.debug("[pyatv_service] is_pairing_needed: no devices found")
        return False
    # Pick the first device and locate its service for the protocol
    device = devices[0]
    # Auto-select protocol: prefer Companion/MRP if present, else AirPlay
    protocol = (
        Protocol.Companion
        if device.get_service(Protocol.Companion)
        else Protocol.AirPlay
    )
    service = device.get_service(protocol)
    logging.debug(
        f"[pyatv_service] Found service: {service} with credentials={getattr(service, 'credentials', None)} and pairing={service.pairing}"
    )
    if not service:
        return False
    # If credentials already exist in storage, pairing not needed
    if getattr(service, "credentials", None):
        return False
    # Determine pairing requirement:
    if protocol == Protocol.AirPlay:
        # On AirPlay, only require pairing if a password is needed
        result = service.pairing == PairingRequirement.Mandatory and getattr(
            service, "requires_password", False
        )
    else:
        # For Companion/MRP or others, pairing is mandatory based solely on pairing flag
        result = service.pairing == PairingRequirement.Mandatory
    logging.debug(
        f"[pyatv_service] is_pairing_needed result: {result} (pairing={service.pairing}, requires_password={getattr(service, 'requires_password', None)})"
    )
    return result


def is_pairing_needed_sync(host: str, protocol: Protocol = Protocol.AirPlay) -> bool:
    """
    Synchronous wrapper around is_pairing_needed.
    """
    future = asyncio.run_coroutine_threadsafe(
        is_pairing_needed(host, protocol), _PAIR_LOOP
    )
    return future.result()


async def remove_pairing() -> bool:
    """
    Remove all stored pairing credentials and settings by clearing the entire storage.
    Returns True if storage was non-empty and is now cleared, False if it was already empty.
    """
    # Log current stored pairings before removal
    await STORAGE.load()
    current = [device for device in STORAGE.storage_model.devices]
    logging.info(f"remove_pairing: existing devices before clear: {current}")
    # Also remove the storage file on disk to ensure credentials are fully wiped
    storage_file = STORAGE._filename  # path to ~/.pyatv.conf
    try:
        os.remove(storage_file)
        logging.debug(f"Removed storage file: {storage_file}")
    except OSError as e:
        logging.error(f"Failed to remove storage file {storage_file}: {e}")
    # Clear in-memory storage model so credentials are truly wiped
    STORAGE.storage_model = StorageModel(
        version=STORAGE.storage_model.version, devices=[]
    )
    # Verify storage_model after clear
    cleared = [device.identifier for device in STORAGE.storage_model.devices]
    logging.info(f"remove_pairing: devices after clear: {cleared}")
    return True


def remove_pairing_sync() -> bool:
    """
    Synchronous wrapper around remove_pairing.
    """
    future = asyncio.run_coroutine_threadsafe(remove_pairing(), _PAIR_LOOP)
    return future.result()
