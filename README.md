# 🎵 Jukebox

# Currently not working / under maintenance.

<p align="center">
  <img src="assets/logo.png" width="200">
</p>

<p align="center">
  <b>Jukebox is a reliable Discord music bot that joins your voice channel and streams high-quality audio from YouTube. Built for smooth playback, low resource usage, and 24/7 hosting. Simple commands, powerful performance — just play and enjoy. 🎶</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Node.js-18+-green">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-blue">
  <img src="https://img.shields.io/badge/Raspberry%20Pi-Ready-orange">
  <img src="https://img.shields.io/badge/License-MIT-purple">
  <img src="https://img.shields.io/badge/Status-Active-success">
</p>

---

# 🚀 About

**Jukebox** is a lightweight and reliable Discord music bot that streams audio from YouTube using `yt-dlp` and `FFmpeg`.

Designed for:
- Stable playback
- Low resource usage
- 24/7 hosting
- Raspberry Pi deployment
- Windows auto-start support

---

# ✨ Features

- ▶ YouTube URL playback
- 🔎 YouTube search support
- 📜 Advanced queue system
- ⏭ Skip tracks
- ⏸ Pause / Resume
- 🔁 Loop support
- ⏱ Auto-leave after 5 minutes inactivity
- ⚡ Optimized with yt-dlp
- 🍪 Cookie support for restricted videos
- 🖥 Windows startup support
- 🍓 Raspberry Pi optimized
- 🐳 Docker compatible

---

# 📦 Commands

| Command | Description |
|----------|------------|
| `/join` | Join your voice channel |
| `/play <url or search>` | Play YouTube link or search |
| `/pause` | Pause current song |
| `/resume` | Resume playback |
| `/skip` | Skip current track |
| `/queue` | Show queue |
| `/loop` | Toggle loop |
| `/leave` | Disconnect |

---

# 🛠 Installation

## 1️⃣ Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/jukebox.git
cd jukebox
```

---

## 2️⃣ Install Dependencies

```bash
npm install
```

---

## 3️⃣ Install System Requirements

### Windows
- Install Node.js 18+
- Install FFmpeg and add to PATH
- Install yt-dlp

### Linux / Raspberry Pi

```bash
sudo apt update
sudo apt install -y ffmpeg python3 python3-pip
pip install -U yt-dlp
```

---

# 🔐 Bot Token Setup

This project reads the bot token from a file called:

```
token.txt
```

Create a file named `token.txt` in the root directory and paste your bot token inside:

```
YOUR_DISCORD_BOT_TOKEN_HERE
```

⚠ Do NOT add quotes or spaces.

---

# ▶ Running Jukebox

```bash
node index.js
```

---

# 🔄 24/7 Hosting (PM2 Recommended)

```bash
npm install -g pm2
pm2 start index.js --name jukebox
pm2 save
pm2 startup
```

Check logs:

```bash
pm2 logs jukebox
```

---

# 🖥 Windows Auto Start (Hidden Mode)

1. Open **Task Scheduler**
2. Create new task
3. Trigger → At Startup
4. Action → Start `run.bat`
5. Enable:
   - ✔ Run whether user is logged in or not
   - ✔ Run with highest privileges
   - ✔ Hidden

---

# 🍓 Raspberry Pi Deployment

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
npm install
pm2 start index.js --name jukebox
pm2 save
```

---

# 📁 Project Structure

```
jukebox/
│
├── commands/
├── events/
├── utils/
├── index.js
├── package.json
├── token.txt
└── README.md
```

---

# 🛠 Troubleshooting

### ❌ Bot not playing audio
- Verify FFmpeg installed
- Verify yt-dlp installed
- Check voice permissions

### ❌ "Sign in to confirm you're not a bot"
- Export YouTube cookies
- Place `cookies.txt` in root folder

### ❌ Bot doesn't join voice
- Check permissions
- Enable required intents in Discord Developer Portal

---

# 📜 License

MIT License

---

<p align="center">
  🎶 Jukebox — Your Discord server’s music engine
</p>
