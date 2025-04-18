# Applepie RPC

Standalone Python daemon that discovers playback from Apple Music (Music.app), HomePod and Apple TV via mDNS/AirPlay, fetches metadata (including artwork via iTunes API), and publishes it to Discord Rich Presence.

---

## Table of Contents

- [Features](#features)  
- [Prerequisites](#prerequisites)  
- [Installation](#installation)  
- [Building the Standalone Binary](#building-the-standalone-binary)  
- [Cache Location](#cache-location)  

---

## Features

- 🎧 Detects playback in:
  - macOS Music.app / iTunes (via AppleScript)  
  - HomePod devices (via AirPlay playback‑info)  
  - Apple TV (via `atvscript` JSON output)  
- 🤖 Publishes “Now Playing” as Discord Rich Presence (via `pypresence`)  
- 🔍 Fetches high‑res album artwork & track URLs from iTunes Lookup API  
- 🐢 Caches metadata to disk for fast lookups  
- 🛠️ Graceful shutdown on SIGINT/SIGTERM, clears RPC state  
- ⚙️ External command interface via `/tmp/applepie_rpc_cmd` for:
  - `PAUSE` / `RESUME`  
  - `INTERVAL:<seconds>`  

---

## Prerequisites

- **macOS 10.13+** (for AppleScript & AirPlay support)  
- **Python 3.8+**  
- **pip** (or `pip3`)  

---

## Installation

Install required Python packages globally or in your preferred environment:

```bash
pip install \
  aiohttp \
  pypresence \
  zeroconf \
  appdirs
```

## Building the Standalone Binary

To build the standalone executable, run:

```bash
pyinstaller \
  --clean \
  --onefile \
  --name applepie-rpc \
  --hidden-import=pypresence.types \
  --hidden-import=zeroconf.asyncio \
  --add-data "$(which atvscript)":. \
  applepie-rpc.py
```

---

## Cache Location

Metadata (artwork URLs & track links) is cached at:

```
~/Library/Caches/Skyline23/applepie_rpc/applepie_cache.db
```