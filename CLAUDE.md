# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Create virtual environment (first time only)
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py
```

## Architecture

This is a Discord music bot built with discord.py that plays audio from YouTube.

### Entry Point
- `bot.py` - Main entry point. Initializes the Discord bot, sets up logging, and loads the music cog. Uses command prefix `?` for legacy commands and supports slash commands.

### Cog Structure
- `cogs/music.py` - Contains all music functionality as a single cog (`Music` class)

### Key Classes in `cogs/music.py`

**Song** - Data class representing a playable track (source URL, title, webpage URL, duration, thumbnail)

**MusicPlayer** - Per-guild state container holding:
- `queue` (deque of Songs)
- `history` (deque of previously played Songs, max 50)
- `current` (currently playing Song)
- `volume` (0.0-1.0)
- `loop_mode` ("off", "one", "all")
- Timing state for progress tracking

**Music (Cog)** - Main cog class containing:
- `self.players: dict[int, MusicPlayer]` - Maps guild ID to player state
- All slash commands (/play, /pause, /skip, etc.)

### Audio Pipeline
1. `get_song()` uses yt-dlp to extract audio URL from YouTube
2. `discord.FFmpegPCMAudio` streams audio through local FFmpeg binary
3. `discord.PCMVolumeTransformer` applies volume control
4. `play_next()` handles queue progression, loop modes, and error recovery

### Error Recovery
- `play_next()` detects premature song endings caused by YouTube 403 errors or streaming failures
- Detection triggers if: song ends in <15 seconds (for songs >20s) OR plays <20% of expected duration
- `_retry_current_song()` waits 1 second, then re-fetches the YouTube URL and retries playback
- Maximum 2 retry attempts per song before skipping to next
- `retry_count` in MusicPlayer tracks attempts, resets on successful playback or new /play command
- Errors from FFmpeg are logged and passed through the callback chain

### Player Message Cleanup
- `cleanup_player()` deletes the now-playing embed when playback stops
- `player_messages.json` persists message IDs so orphaned embeds can be cleaned up after bot restarts
- `_cleanup_orphaned_messages()` runs on cog load to delete leftover embeds from previous sessions
- Cleanup triggers: `/leave`, `/stop`, queue ends, auto-disconnect, bot shutdown

### FFmpeg
The bot automatically detects the appropriate FFmpeg executable:
- **Linux/macOS**: Uses system FFmpeg from PATH (install via package manager)
- **Windows**: Uses bundled FFmpeg at `ffmpeg/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe`

On Bazzite/Fedora, FFmpeg is typically pre-installed. If not, install via:
```bash
rpm-ostree install ffmpeg
```

### Audio Stability Optimizations
The bot includes several optimizations to prevent audio issues like speed-up glitches or dropouts:

**FFmpeg Options:**
- `-nostdin` - Prevents FFmpeg from waiting for stdin input, avoiding hangs
- `-reconnect 1` - Enables automatic reconnection on stream interruption
- `-reconnect_streamed 1` - Enables reconnection for streamed content
- `-reconnect_delay_max 5` - Maximum delay between reconnection attempts (5 seconds)
- `-reconnect_on_network_error 1` - Reconnect on network errors
- `-reconnect_on_http_error 4xx,5xx` - Reconnect on HTTP errors
- `-vn -sn -dn` - Disables video, subtitle, and data streams for cleaner audio processing

**Connection Stability:**
- 0.5 second delay after joining voice channel allows the connection to stabilize before playback
- Applied to both `/join` and `/play` commands when connecting to a new channel

### Generated Files
- `bot.log` - Debug logging output (recreated on each run)
- `player_messages.json` - Tracks active player embeds for cleanup on restart

### Environment Variables
- `DISCORD_TOKEN` (required) - Bot authentication token

## Pre-Use Hook: Slash Command Protection

**IMPORTANT:** Before modifying any slash commands in `cogs/music.py`:
1. Do NOT remove or rename existing slash commands unless explicitly requested by the user
2. Do NOT modify the core functionality of existing commands without user approval
3. When adding new functionality to an existing command, ensure backwards compatibility
4. If a change would break existing behavior, ask the user first

Protected commands that should not be removed or have their core functionality changed without explicit user request:
- `/play` - Play a song
- `/pause` - Pause playback
- `/resume` - Resume playback
- `/stop` - Stop and clear queue
- `/skip` - Skip current song
- `/previous` - Go back to previous song
- `/restart` - Restart current song
- `/queue` - Show queue
- `/nowplaying` - Show current song
- `/volume` - Set volume
- `/shuffle` - Shuffle queue
- `/loop` - Set loop mode
- `/seek` - Seek to position
- `/remove` - Remove from queue
- `/clear` - Clear queue
- `/join` - Join voice channel
- `/leave` - Leave voice channel
- `/help` - Show all commands

## Post-Use Hook: Help Command Maintenance

**IMPORTANT:** After ANY of the following changes to `cogs/music.py`, you MUST update the `/help` command:
1. Adding a new slash command
2. Removing a slash command
3. Renaming a slash command
4. Changing what a command does

The `/help` command is located at the end of the `Music` class in `cogs/music.py`. It contains three embed fields:
- 🎵 **Playback** - Commands for controlling playback
- 📋 **Queue** - Commands for managing the queue
- 🔊 **Voice & Settings** - Commands for voice channel and settings

When updating, ensure the command list in `/help` matches the actual available commands.
