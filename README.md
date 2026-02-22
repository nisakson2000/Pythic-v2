# Discord Music Bot

A full-featured Discord music bot that plays music from YouTube with interactive controls.

## Features

- **Playback Controls**: play, pause, resume, stop, skip, previous, restart, seek
- **Queue Management**: view queue, shuffle, remove songs, clear
- **Loop Modes**: off, single track, entire queue
- **Volume Control**: 0-100%
- **Interactive Player**: embedded now-playing display with button controls
- **Progress Tracking**: real-time progress bar and time remaining
- **Auto-Recovery**: automatically retries playback on connection errors
- **Auto-Cleanup**: player embeds are cleaned up when playback stops or bot restarts
- **Auto-Disconnect**: leaves voice channel after 2 minutes of inactivity

## Requirements

- Python 3.10+
- FFmpeg (system-installed)
- Discord bot token

## Setup

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Ensure FFmpeg is installed:
   ```bash
   # Fedora/Bazzite (usually pre-installed)
   ffmpeg -version

   # If not installed on Fedora Atomic (Bazzite):
   rpm-ostree install ffmpeg

   # Ubuntu/Debian:
   sudo apt install ffmpeg
   ```

4. Create a `.env` file:
   ```
   DISCORD_TOKEN=your_token_here
   ```

5. Run the bot:
   ```bash
   python bot.py
   ```

## Slash Commands

All commands use Discord's slash command system (type `/` to see them).

### Playback

| Command | Description |
|---------|-------------|
| `/play <query>` | Play a song from YouTube (supports search or URL) |
| `/pause` | Pause the current song |
| `/resume` | Resume playback |
| `/stop` | Stop playing and clear the queue |
| `/skip` | Skip to the next song |
| `/previous` | Go back to the previous song |
| `/restart` | Restart the current song from the beginning |
| `/seek <timestamp>` | Seek to position (MM:SS or seconds) |

### Queue

| Command | Description |
|---------|-------------|
| `/queue` | Show the current queue |
| `/nowplaying` | Show the currently playing song with controls |
| `/refresh` | Refresh the player to show current progress |
| `/shuffle` | Shuffle the queue |
| `/remove <position>` | Remove a song from the queue |
| `/clear` | Clear the entire queue |
| `/loop <mode>` | Set loop mode (off/one/all) |

### Voice & Settings

| Command | Description |
|---------|-------------|
| `/join` | Join your voice channel |
| `/leave` | Leave the voice channel |
| `/volume <0-100>` | Set the volume |
| `/help` | Show all available commands |

## Interactive Player Controls

When a song plays, an embedded player appears with button controls:

- **Shuffle** - Shuffle the queue
- **Previous** - Go to previous song
- **Pause/Play** - Toggle playback
- **Skip** - Skip to next song
- **Loop** - Cycle through loop modes
- **Stop** - Stop playback and clear queue
- **Refresh** - Update the progress display

## Getting a Discord Bot Token

1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to "Bot" section and create a bot
4. Copy the token
5. Enable "Message Content Intent" under Privileged Gateway Intents
6. Go to OAuth2 > URL Generator
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Manage Messages`
9. Use the generated URL to invite the bot to your server
