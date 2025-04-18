# Applepie RPC

Standalone Python daemon that discovers playback from Apple Music (Music.app), HomePod and Apple TV via mDNS/AirPlay, fetches metadata (including artwork via iTunes API), and publishes it to Discord Rich Presence.

---

## Table of Contents

- [Features](#features)  
- [Prerequisites](#prerequisites)  
- [Installation](#installation)  
- [Building the Standalone Binary](#building-the-standalone-binary)  

- [Usage](#usage)  
- [Cache Location](#cache-location)  
- [Configuration & Commands](#configuration--commands)  
- [Troubleshooting](#troubleshooting)  
- [Contributing](#contributing)  
- [License](#license)  

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

To build the standalone executable, run:

```bash
pyinstaller \
  --clean \
  --onefile \
  --name applepie-rpc \
  --hidden-import pypresence.types \
  --hidden-import zeroconf.asyncio \
  applepie-rpc.py
```