<h1 align="center">Pythic</h1>
<p align="center">A Discord music bot built with discord.py — plays YouTube audio with interactive controls, queue management, and playlist support.</p>

---

## Features

**Playback** — play, pause, resume, stop, skip, previous, restart, seek with real-time progress tracking

**Queue** — view, shuffle, move, remove, clear, loop (off / one / all) with paginated display

**Playlists** — load full YouTube playlists from any URL format

**Search** — search YouTube and pick from the top 10 results via dropdown

**Interactive Player** — embedded now-playing display with button controls (shuffle, prev, pause/play, skip, loop, stop, refresh)

**Smart Defaults** — volume persists across restarts, auto-recovery on connection errors, auto-disconnect after 2 min idle, auto-cleanup of player embeds

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Ensure FFmpeg is available
ffmpeg -version
# Fedora Atomic/Bazzite: rpm-ostree install ffmpeg
# Ubuntu/Debian: sudo apt install ffmpeg

# 4. Set your bot token
echo "DISCORD_TOKEN=your_token_here" > .env

# 5. Run
python bot.py
```

> Requires **Python 3.10+**, **FFmpeg**, and a **Discord bot token**.

## Commands

All commands use Discord's slash command system — type `/` to see them.

### Playback

| Command | Description |
|---|---|
| `/play <query>` | Play a song or playlist (search, URL, or playlist URL) |
| `/playnext <query>` | Insert a song at the front of the queue |
| `/pause` | Pause the current song |
| `/resume` | Resume playback |
| `/stop` | Stop and clear the queue |
| `/skip` | Skip to the next song |
| `/previous` | Go back to the previous song |
| `/restart` | Restart the current song |
| `/seek <timestamp>` | Seek to position (`MM:SS` or seconds) |

### Queue

| Command | Description |
|---|---|
| `/queue` | Show the current queue |
| `/nowplaying` | Show the current song with controls |
| `/refresh` | Refresh the player progress display |
| `/shuffle` | Shuffle the queue |
| `/move <from> <to>` | Move a song in the queue |
| `/remove <position>` | Remove a song from the queue |
| `/clear` | Clear the queue |
| `/loop [mode]` | Cycle or set loop mode (`off` / `one` / `all`) |

### Voice & Settings

| Command | Description |
|---|---|
| `/join` | Join your voice channel |
| `/leave` | Leave the voice channel |
| `/volume <0-100>` | Set playback volume (persists across restarts) |
| `/help` | Show all commands |

## Bot Token Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) and create a new application
2. **Bot** section — create a bot, copy the token, enable **Message Content Intent**
3. **OAuth2 > URL Generator** — select scopes `bot` + `applications.commands`
4. Select permissions: **Connect**, **Speak**, **Send Messages**, **Embed Links**, **Manage Messages**
5. Use the generated URL to invite the bot to your server
