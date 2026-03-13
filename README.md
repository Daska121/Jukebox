# 🎵 Jukebox — Discord Music Bot

A self-hosted Discord music bot written in Python.
Plays YouTube audio in voice channels with full support for Discord's **DAVE E2E encryption** (enforced by Discord since March 2026).

---

## Features

- Play YouTube videos by URL or search query
- Per-server queue with skip, pause, resume, and stop
- Auto-leaves voice after 5 minutes of inactivity
- Full DAVE (Discord Audio & Video E2E Encryption / MLS) support via `dave.py`
- HLS streams handled via a yt-dlp pipe to avoid 403 on signed segment URLs
- Next song pre-buffered in the background for near-instant transitions

---

## Prerequisites

| Dependency | Version tested | Notes |
|---|---|---|
| Python | 3.14 (64-bit) | Must match the `dave.py` wheel |
| discord.py | 2.7.1 | `pip install discord.py[voice]` |
| dave.py | 0.1.2 | DisnakeDev's libdave bindings — replaces `davey` |
| davey | 0.1.4 | Still required by discord.py's import; patched at runtime |
| yt-dlp | ≥ 2026.2.4 | `pip install yt-dlp` |
| FFmpeg | any recent | Must be on `PATH` |
| PyNaCl | 1.6.2 | Transport encryption (installed with discord.py[voice]) |

> **Why `davey_compat.py`?**
> discord.py imports `davey` at startup. `davey 0.1.4` (Snazzah's Rust implementation)
> produces MLS frames that Discord's client cannot decrypt, causing silent audio.
> `davey_compat.py` is a shim that monkey-patches `discord.voice_state.davey` and
> `discord.gateway.davey` to use `dave.py` (DisnakeDev's official C++ libdave bindings)
> instead, while exposing the same API discord.py expects.

---

## Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/yourname/jukebox.git
   cd jukebox
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements_bot.txt
   ```

3. **Create `token.txt`** in the project folder with your bot token on the first line:

   ```
   YOUR_BOT_TOKEN_HERE
   ```

4. **Create `cookies.txt`** (Netscape format) — required for age-restricted or
   region-locked YouTube videos.
   Export it from your browser with a cookie-export extension (e.g. *Get cookies.txt LOCALLY*).

5. **Run**

   ```bash
   python main.py
   ```

---

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!play <url or search>` | `!p` | Play or queue a song |
| `!skip` | `!s`, `!next` | Skip current track |
| `!pause` | — | Pause playback |
| `!resume` | `!r` | Resume playback |
| `!stop` | — | Stop and clear the queue |
| `!queue` | `!q` | Show the current queue |
| `!np` | `!nowplaying` | Show the currently playing track |
| `!join` | `!j` | Join your voice channel |
| `!leave` | `!l`, `!dc` | Leave voice and clear the queue |
| `!test` | — | Play a 5-second 440 Hz test tone (verifies audio pipeline) |
| `!help` | — | Show the help embed |

---

## Project Structure

```
main.py           — Bot entry point; patches davey → davey_compat at startup
music_cog.py      — All music commands and playback logic
davey_compat.py   — DAVE E2E encryption shim (dave.py wrapping the davey API)
token.txt         — Bot token (not committed)
cookies.txt       — YouTube cookies (not committed)
requirements_bot.txt — Minimal dependency list for the bot
```

---

## Technical Notes

### DAVE E2E Encryption

Discord enforces the DAVE protocol (MLS-based E2E encryption) in all voice channels
since March 2, 2026.  Every bot must complete the MLS handshake or receive error 4017.

The shim (`davey_compat.py`) handles:
- MLS session init, proposals, commits, and welcomes via `libdave`
- Recognising all current channel members so libdave can validate their credentials
- Opus frame encryption through libdave's `Encryptor` before discord.py's
  transport layer (XChaCha20-Poly1305) wraps it

### HLS Audio Delivery

As of early 2026, yt-dlp returns only HLS (m3u8) streams for most YouTube videos.
FFmpeg cannot fetch the signed segment URLs directly (403 Forbidden) because the
URLs are IP-locked to the IPv4 address used at extraction time, and FFmpeg may
prefer IPv6.

The solution: yt-dlp runs as a subprocess with `--source-address 0.0.0.0` (forcing
IPv4) and pipes its audio output to FFmpeg's stdin, so yt-dlp handles all
authentication and segment fetching.

To reduce the gap between songs the next song's yt-dlp subprocess is pre-started
while the current song is still playing.
