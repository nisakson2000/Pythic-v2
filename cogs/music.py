import asyncio
import logging
import random
import subprocess
import time
import json
from collections import deque
from typing import Optional, List
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import os
import sys
import shutil

logger = logging.getLogger('discord-bot.music')

# File to persist player message IDs for cleanup on restart
PLAYER_MESSAGES_FILE = Path(__file__).parent.parent / "player_messages.json"

# Determine FFmpeg path based on platform
def get_ffmpeg_path() -> str:
    """Get the FFmpeg executable path for the current platform."""
    if sys.platform == 'win32':
        # Windows: use bundled FFmpeg
        bundled_path = Path(__file__).parent.parent / "ffmpeg" / "ffmpeg-8.0.1-essentials_build" / "bin" / "ffmpeg.exe"
        if bundled_path.exists():
            return str(bundled_path)

    # Linux/macOS: use system FFmpeg from PATH
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg

    # Fallback: hope 'ffmpeg' is in PATH
    return 'ffmpeg'

FFMPEG_PATH = get_ffmpeg_path()

YDL_OPTIONS = {
    'format': 'bestaudio[acodec=opus][abr>128]/bestaudio[abr>128]/bestaudio/best',  # Prefer high bitrate opus
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extract_flat': False,
}

YDL_SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch5',
    'extract_flat': True,
}

FFMPEG_OPTIONS = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
    'options': '-vn -sn -dn',  # Disable video, subtitle, and data streams for cleaner audio
    'stderr': subprocess.DEVNULL,  # Suppress FFmpeg error output from terminal
}


class Song:
    def __init__(self, source: str, title: str, url: str, duration: int, thumbnail: str):
        self.source = source
        self.title = title
        self.url = url
        self.duration = duration
        self.thumbnail = thumbnail

    @staticmethod
    def format_duration(seconds: int) -> str:
        if not seconds:
            return "0:00"
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"


class MusicPlayer:
    def __init__(self):
        self.queue: deque[Song] = deque()
        self.history: deque[Song] = deque(maxlen=50)  # Track up to 50 previous songs
        self.current: Optional[Song] = None
        self.volume: float = 0.5
        self.loop_mode: str = "off"  # off, one, all
        self.start_time: float = 0
        self.paused_time: float = 0
        self.is_paused: bool = False
        self.skip_next_callback: bool = False  # Prevents play_next from running when manually stopping
        self.player_message: Optional[discord.Message] = None  # Track the current embedded player message
        self.retry_count: int = 0  # Track retry attempts for failed playback
        self.text_channel: Optional[discord.TextChannel] = None  # Track last used text channel for notifications


AUTO_DISCONNECT_DELAY = 120  # Seconds to wait before disconnecting when idle


class PlayerView(discord.ui.View):
    """Interactive player controls with premium styling"""
    def __init__(self, cog: 'Music', guild_id: int, is_paused: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self._update_buttons(is_paused)

    def _update_buttons(self, is_paused: bool):
        """Update button states based on player state"""
        player = self.cog.get_player(self.guild_id)
        # Update pause/play button emoji
        self.pause_resume_button.emoji = "▶️" if is_paused else "⏸️"
        # Update loop button style based on mode
        if player.loop_mode == "off":
            self.loop_button.style = discord.ButtonStyle.secondary
            self.loop_button.emoji = "🔁"
        elif player.loop_mode == "one":
            self.loop_button.style = discord.ButtonStyle.success
            self.loop_button.emoji = "🔂"
        else:  # all
            self.loop_button.style = discord.ButtonStyle.success
            self.loop_button.emoji = "🔁"

    async def _update_embed(self, interaction: discord.Interaction):
        """Update the embed with current state"""
        player = self.cog.get_player(self.guild_id)
        if not player.current:
            return
        embed = self.cog.create_now_playing_embed(player)
        self._update_buttons(player.is_paused)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if len(player.queue) < 2:
            return await interaction.response.send_message("Need at least 2 songs in queue to shuffle!", ephemeral=True)
        queue_list = list(player.queue)
        random.shuffle(queue_list)
        player.queue = deque(queue_list)
        await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("Not in a voice channel!", ephemeral=True)

        player = self.cog.get_player(self.guild_id)
        if not player.history:
            return await interaction.response.send_message("No previous songs!", ephemeral=True)

        previous_song = player.history.pop()
        if player.current:
            player.queue.appendleft(player.current)
        player.current = previous_song

        if vc.is_playing() or vc.is_paused():
            player.skip_next_callback = True
            vc.stop()

        source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)
        player.start_time = time.time()
        player.is_paused = False
        vc.play(source, after=lambda e: self.cog.play_next(self.guild_id, vc, interaction.channel, error=e))

        await interaction.response.defer()

        # Delete old player message
        if player.player_message:
            try:
                await player.player_message.delete()
            except:
                pass

        # Send text message for history, then new embed below it
        await interaction.channel.send(f"⏮️ **Now Playing:** {player.current.title}")
        embed = self.cog.create_now_playing_embed(player)
        self._update_buttons(player.is_paused)
        player.player_message = await interaction.channel.send(embed=embed, view=self)
        self.cog._save_player_message(self.guild_id, interaction.channel.id, player.player_message.id)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary)
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("Not in a voice channel!", ephemeral=True)

        player = self.cog.get_player(self.guild_id)

        if vc.is_playing():
            vc.pause()
            player.paused_time = time.time()
            player.is_paused = True
        elif vc.is_paused():
            vc.resume()
            pause_duration = time.time() - player.paused_time
            player.start_time += pause_duration
            player.is_paused = False
        else:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        await interaction.response.defer()
        await self._update_embed(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        player = self.cog.get_player(self.guild_id)
        if player.loop_mode == "one":
            player.loop_mode = "off"

        await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        vc.stop()

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        # Cycle through loop modes: off -> one -> all -> off
        if player.loop_mode == "off":
            player.loop_mode = "one"
            msg = "🔂 Looping current song"
        elif player.loop_mode == "one":
            player.loop_mode = "all"
            msg = "🔁 Looping queue"
        else:
            player.loop_mode = "off"
            msg = "Loop disabled"

        await interaction.response.defer()
        await self._update_embed(interaction)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("Not in a voice channel!", ephemeral=True)

        player = self.cog.get_player(self.guild_id)
        player.queue.clear()
        player.current = None
        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await interaction.response.defer()
        # Delete the now playing message silently
        try:
            await interaction.message.delete()
        except:
            pass

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if not player.current:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        await interaction.response.defer()
        await self._update_embed(interaction)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, MusicPlayer] = {}
        self.disconnect_tasks: dict[int, asyncio.Task] = {}  # Track disconnect timers per guild

    async def cog_load(self):
        """Called when the cog is loaded - clean up any orphaned player messages from previous sessions"""
        await self._cleanup_orphaned_messages()

    def _save_player_message(self, guild_id: int, channel_id: int, message_id: int):
        """Save player message info to file for cleanup on restart"""
        try:
            data = {}
            if PLAYER_MESSAGES_FILE.exists():
                with open(PLAYER_MESSAGES_FILE, 'r') as f:
                    data = json.load(f)
            data[str(guild_id)] = {'channel_id': channel_id, 'message_id': message_id}
            with open(PLAYER_MESSAGES_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error saving player message: {e}")

    def _remove_player_message(self, guild_id: int):
        """Remove player message info from file"""
        try:
            if PLAYER_MESSAGES_FILE.exists():
                with open(PLAYER_MESSAGES_FILE, 'r') as f:
                    data = json.load(f)
                if str(guild_id) in data:
                    del data[str(guild_id)]
                    with open(PLAYER_MESSAGES_FILE, 'w') as f:
                        json.dump(data, f)
        except Exception as e:
            logger.error(f"Error removing player message: {e}")

    async def _cleanup_orphaned_messages(self):
        """Clean up any player messages left over from a previous bot session"""
        if not PLAYER_MESSAGES_FILE.exists():
            return

        try:
            with open(PLAYER_MESSAGES_FILE, 'r') as f:
                data = json.load(f)

            for guild_id_str, info in list(data.items()):
                try:
                    channel = self.bot.get_channel(info['channel_id'])
                    if channel:
                        try:
                            message = await channel.fetch_message(info['message_id'])
                            await message.delete()
                            logger.info(f"Cleaned up orphaned player message in guild {guild_id_str}")
                        except discord.NotFound:
                            pass  # Message already deleted
                        except discord.Forbidden:
                            logger.warning(f"No permission to delete orphaned message in guild {guild_id_str}")
                except Exception as e:
                    logger.error(f"Error cleaning up orphaned message for guild {guild_id_str}: {e}")

            # Clear the file after cleanup
            with open(PLAYER_MESSAGES_FILE, 'w') as f:
                json.dump({}, f)
        except Exception as e:
            logger.error(f"Error during orphaned message cleanup: {e}")

    def get_player(self, guild_id: int) -> MusicPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = MusicPlayer()
        return self.players[guild_id]

    async def cleanup_player(self, guild_id: int):
        """Clean up player state and delete the player message"""
        if guild_id in self.players:
            player = self.players[guild_id]
            # Delete the player message if it exists
            if player.player_message:
                try:
                    await player.player_message.delete()
                except Exception:
                    pass
                player.player_message = None
        # Always remove from persistent storage
        self._remove_player_message(guild_id)

    async def cog_unload(self):
        """Called when the cog is unloaded (bot shutdown, cog reload, etc.)"""
        # Clean up all player messages
        for guild_id in list(self.players.keys()):
            await self.cleanup_player(guild_id)
        # Cancel all disconnect timers
        for task in self.disconnect_tasks.values():
            task.cancel()
        self.disconnect_tasks.clear()

    def cancel_disconnect_timer(self, guild_id: int):
        """Cancel any pending disconnect timer for a guild"""
        if guild_id in self.disconnect_tasks:
            self.disconnect_tasks[guild_id].cancel()
            del self.disconnect_tasks[guild_id]

    async def start_disconnect_timer(self, guild_id: int, voice_client: discord.VoiceClient):
        """Start a timer to disconnect after inactivity"""
        self.cancel_disconnect_timer(guild_id)

        async def disconnect_after_delay():
            await asyncio.sleep(AUTO_DISCONNECT_DELAY)
            if voice_client and voice_client.is_connected():
                # Get text channel for notification before cleanup
                player = self.players.get(guild_id)
                text_channel = player.text_channel if player else None

                # Clean up player message before disconnecting
                await self.cleanup_player(guild_id)
                if guild_id in self.players:
                    del self.players[guild_id]
                await voice_client.disconnect()

                # Send disconnect notification
                if text_channel:
                    try:
                        await text_channel.send("👋 Disconnected due to inactivity.")
                    except:
                        pass

        self.disconnect_tasks[guild_id] = asyncio.create_task(disconnect_after_delay())

    async def search_songs(self, query: str) -> List[dict]:
        """Search YouTube and return list of results for autocomplete"""
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YDL_SEARCH_OPTIONS) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(f"ytsearch5:{query}", download=False))
                if 'entries' in info:
                    return [{'title': e['title'][:80], 'url': e['url']} for e in info['entries'] if e]
        except Exception:
            pass
        return []

    async def get_song(self, query: str) -> Optional[Song]:
        """Get full song info from URL or search query"""
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            try:
                if not query.startswith(('http://', 'https://')):
                    query = f"ytsearch:{query}"
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                return Song(
                    source=info['url'],
                    title=info['title'],
                    url=info['webpage_url'],
                    duration=info.get('duration', 0),
                    thumbnail=info.get('thumbnail', '')
                )
            except Exception as e:
                logger.error(f"Error getting song: {e}")
                return None

    def play_next(self, guild_id: int, voice_client: discord.VoiceClient, text_channel: discord.TextChannel, announce: bool = True, error: Exception = None):
        player = self.get_player(guild_id)

        # Log any playback errors
        if error:
            logger.error(f"Playback error for guild {guild_id}: {error}")

        # Skip this callback if we're manually stopping (e.g., for restart/previous)
        if player.skip_next_callback:
            player.skip_next_callback = False
            return

        # Check if song ended too quickly (likely a playback error)
        # This catches YouTube 403 errors and other streaming failures
        if player.current and player.start_time > 0 and player.retry_count < 2:
            elapsed = time.time() - player.start_time
            expected_duration = player.current.duration
            # Detect failure if:
            # 1. Song ended in less than 15 seconds (FFmpeg retry timeout) AND song should be longer than 20s
            # 2. OR song played less than 20% of its expected duration (for shorter songs)
            is_premature_end = (
                (elapsed < 15 and expected_duration > 20) or
                (expected_duration > 0 and elapsed < expected_duration * 0.2 and elapsed < 30)
            )
            if is_premature_end:
                logger.warning(f"Song ended prematurely ({elapsed:.1f}s of {expected_duration}s), likely playback error. Retry {player.retry_count + 1}/2...")
                player.retry_count += 1
                # Retry playing the same song by re-fetching the URL
                asyncio.run_coroutine_threadsafe(
                    self._retry_current_song(guild_id, voice_client, text_channel),
                    self.bot.loop
                )
                return

        # Reset retry count on successful playback or when moving to next song
        player.retry_count = 0

        if player.loop_mode == "one" and player.current:
            source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=player.volume)
            voice_client.play(source, after=lambda e: self.play_next(guild_id, voice_client, text_channel, error=e))
            player.start_time = time.time()
            return

        # Add current song to history before moving on
        if player.current:
            player.history.append(player.current)

        if player.loop_mode == "all" and player.current:
            player.queue.append(player.current)

        if not player.queue:
            player.current = None
            # Clean up player message and start auto-disconnect timer when queue ends
            asyncio.run_coroutine_threadsafe(
                self.cleanup_player(guild_id),
                self.bot.loop
            )
            asyncio.run_coroutine_threadsafe(
                self.start_disconnect_timer(guild_id, voice_client),
                self.bot.loop
            )
            return

        player.current = player.queue.popleft()
        source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)
        voice_client.play(source, after=lambda e: self.play_next(guild_id, voice_client, text_channel, error=e))
        player.start_time = time.time()
        player.is_paused = False

        if announce:
            asyncio.run_coroutine_threadsafe(
                self.send_now_playing(guild_id, text_channel, player.current),
                self.bot.loop
            )

    async def _retry_current_song(self, guild_id: int, voice_client: discord.VoiceClient, text_channel: discord.TextChannel):
        """Retry playing the current song by re-fetching the URL"""
        player = self.get_player(guild_id)
        if not player.current:
            return

        # Brief delay before retry to avoid hammering YouTube
        await asyncio.sleep(1.0)

        # Check if voice client is still valid
        if not voice_client or not voice_client.is_connected():
            logger.warning("Voice client disconnected, cannot retry")
            return

        # Re-fetch the song to get a fresh URL
        fresh_song = await self.get_song(player.current.url)
        if fresh_song:
            player.current = fresh_song
            source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=player.volume)
            player.start_time = time.time()
            voice_client.play(source, after=lambda e: self.play_next(guild_id, voice_client, text_channel, error=e))
        else:
            logger.warning("Failed to retry song, skipping to next")
            player.retry_count = 0  # Reset before moving to next song
            self.play_next(guild_id, voice_client, text_channel)

    def create_now_playing_embed(self, player: MusicPlayer) -> discord.Embed:
        """Create a premium-styled now playing embed"""
        song = player.current
        if not song:
            return discord.Embed(title="Nothing Playing", color=discord.Color.dark_grey())

        # Calculate elapsed time
        if player.start_time == 0:
            # Song hasn't started yet
            elapsed = 0
        elif player.is_paused:
            elapsed = int(player.paused_time - player.start_time)
        else:
            elapsed = int(time.time() - player.start_time)
        elapsed = max(0, min(elapsed, song.duration))  # Clamp to valid range

        # Create wider progress bar (25 characters)
        total_bars = 25
        filled = int((elapsed / song.duration) * total_bars) if song.duration else 0
        progress_bar = "▓" * filled + "░" * (total_bars - filled)

        # Time display
        elapsed_str = Song.format_duration(elapsed)
        total_str = Song.format_duration(song.duration)
        remaining = song.duration - elapsed
        remaining_str = Song.format_duration(remaining)

        # Status indicator
        if player.is_paused:
            status = "⏸️ Paused"
            color = discord.Color.orange()
        else:
            status = "▶️ Now Playing"
            color = discord.Color.green()

        # Loop mode indicator
        loop_indicator = ""
        if player.loop_mode == "one":
            loop_indicator = " 🔂"
        elif player.loop_mode == "all":
            loop_indicator = " 🔁"

        embed = discord.Embed(color=color)
        embed.set_author(name=f"{status}{loop_indicator}", icon_url="https://i.imgur.com/3C5T4Yx.png")

        # Song title as main content
        embed.title = song.title
        embed.url = song.url

        # Progress bar and time - wider bar
        embed.description = f"`{elapsed_str}` {progress_bar} `{total_str}`\n\n`-{remaining_str}` remaining"

        # Large artwork
        if song.thumbnail:
            embed.set_image(url=song.thumbnail)

        # Queue info
        queue_count = len(player.queue)
        if queue_count > 0:
            embed.set_footer(text=f"📋 {queue_count} song{'s' if queue_count != 1 else ''} in queue  •  🔊 {int(player.volume * 100)}%")
        else:
            embed.set_footer(text=f"🔊 Volume: {int(player.volume * 100)}%")

        return embed

    async def send_now_playing(self, guild_id: int, channel: discord.TextChannel, song: Song):
        """Send now playing embed with player controls"""
        player = self.get_player(guild_id)

        # Delete old player message if exists
        if player.player_message:
            try:
                await player.player_message.delete()
                player.player_message = None
            except Exception as e:
                logger.debug(f"Failed to delete old player message: {e}")
                player.player_message = None

        embed = self.create_now_playing_embed(player)
        view = PlayerView(self, guild_id, player.is_paused)
        # Send text message for chat history, then embed with controls
        await channel.send(f"▶️ **Now Playing:** {song.title}")
        player.player_message = await channel.send(embed=embed, view=view)
        self._save_player_message(guild_id, channel.id, player.player_message.id)

    async def song_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for song search"""
        if len(current) < 2:
            return []

        results = await self.search_songs(current)
        return [
            app_commands.Choice(name=r['title'][:100], value=r['url'])
            for r in results[:5]
        ]

    @app_commands.command(name="play", description="Play a song from YouTube")
    @app_commands.describe(query="Song name or YouTube URL")
    @app_commands.autocomplete(query=song_autocomplete)
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)

        await interaction.response.defer()

        voice_client = interaction.guild.voice_client
        if not voice_client:
            try:
                voice_client = await interaction.user.voice.channel.connect(timeout=30.0)
                # Brief delay to allow voice connection to stabilize
                await asyncio.sleep(0.5)
            except asyncio.TimeoutError:
                return await interaction.followup.send("Failed to connect to voice channel. Check bot permissions.")
            except Exception as e:
                return await interaction.followup.send(f"Failed to connect: {str(e)}")

        player = self.get_player(interaction.guild.id)
        self.cancel_disconnect_timer(interaction.guild.id)  # Cancel any pending disconnect
        player.retry_count = 0  # Reset retry counter for new song request
        player.text_channel = interaction.channel  # Track channel for notifications
        song = await self.get_song(query)

        if not song:
            return await interaction.followup.send("Could not find the song.")

        player.queue.append(song)

        if not voice_client.is_playing() and not voice_client.is_paused():
            self.play_next(interaction.guild.id, voice_client, interaction.channel, announce=False)

            # Delete old player message if exists
            if player.player_message:
                try:
                    await player.player_message.delete()
                except:
                    pass

            # Send text message for chat history
            await interaction.followup.send(f"▶️ **Now Playing:** {song.title}")

            # Send premium embed with player controls
            embed = self.create_now_playing_embed(player)
            view = PlayerView(self, interaction.guild.id, player.is_paused)
            player.player_message = await interaction.channel.send(embed=embed, view=view)
            self._save_player_message(interaction.guild.id, interaction.channel.id, player.player_message.id)
        else:
            # Show queued song info as text
            position = len(player.queue)
            await interaction.followup.send(f"📋 **Added to Queue:** {song.title} (#{position})")

    @app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)

        await interaction.response.defer()

        channel = interaction.user.voice.channel
        try:
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(channel)
            else:
                await channel.connect(timeout=30.0)
            # Brief delay to allow voice connection to stabilize
            await asyncio.sleep(0.5)
            await interaction.followup.send(f"Joined **{channel.name}**")
        except asyncio.TimeoutError:
            await interaction.followup.send("Failed to connect to voice channel. Check bot permissions.")

    @app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I'm not in a voice channel!", ephemeral=True)

        # Clean up player message before removing player state
        await self.cleanup_player(interaction.guild.id)
        if interaction.guild.id in self.players:
            del self.players[interaction.guild.id]

        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice channel.")

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        vc.pause()
        player.paused_time = time.time()
        player.is_paused = True
        await interaction.response.send_message("Paused")

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            return await interaction.response.send_message("Nothing is paused!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        vc.resume()
        pause_duration = time.time() - player.paused_time
        player.start_time += pause_duration
        player.is_paused = False
        await interaction.response.send_message("Resumed")

    @app_commands.command(name="stop", description="Stop playing and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I'm not in a voice channel!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        player.queue.clear()
        player.current = None
        player.loop_mode = "off"
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Stopped and cleared the queue.")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        if player.loop_mode == "one":
            player.loop_mode = "off"

        vc.stop()
        await interaction.response.send_message("Skipped")

    @app_commands.command(name="restart", description="Restart the current song from the beginning")
    async def restart(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        if not player.current:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        await interaction.response.defer()

        player.skip_next_callback = True
        vc.stop()

        source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)

        player.start_time = time.time()
        player.is_paused = False

        vc.play(source, after=lambda e: self.play_next(interaction.guild.id, vc, interaction.channel, error=e))
        await interaction.followup.send(f"Restarted **{player.current.title}**")

    @app_commands.command(name="previous", description="Go back to the previous song")
    async def previous(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("I'm not in a voice channel!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        if not player.history:
            return await interaction.response.send_message("No previous songs in history!", ephemeral=True)

        await interaction.response.defer()

        # Get the previous song from history
        previous_song = player.history.pop()

        # Put current song back at the front of the queue if there is one
        if player.current:
            player.queue.appendleft(player.current)

        player.current = previous_song

        # Stop current playback
        if vc.is_playing() or vc.is_paused():
            player.skip_next_callback = True
            vc.stop()

        source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)

        player.start_time = time.time()
        player.is_paused = False

        vc.play(source, after=lambda e: self.play_next(interaction.guild.id, vc, interaction.channel, error=e))
        await interaction.followup.send(f"Now playing: **{player.current.title}**")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)

        if not player.current and not player.queue:
            return await interaction.response.send_message("The queue is empty!", ephemeral=True)

        embed = discord.Embed(title="Music Queue", color=discord.Color.blue())

        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{player.current.title}** [{Song.format_duration(player.current.duration)}]",
                inline=False
            )

        if player.queue:
            queue_list = []
            for i, song in enumerate(list(player.queue)[:10], 1):
                queue_list.append(f"`{i}.` {song.title} [{Song.format_duration(song.duration)}]")

            if len(player.queue) > 10:
                queue_list.append(f"... and {len(player.queue) - 10} more")

            embed.add_field(name="Up Next", value="\n".join(queue_list), inline=False)

        embed.set_footer(text=f"Loop: {player.loop_mode} | Volume: {int(player.volume * 100)}%")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)

        if not player.current:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        # Delete old player message if exists
        if player.player_message:
            try:
                await player.player_message.delete()
            except:
                pass

        embed = self.create_now_playing_embed(player)
        view = PlayerView(self, interaction.guild.id, player.is_paused)
        await interaction.response.send_message(embed=embed, view=view)
        player.player_message = await interaction.original_response()
        self._save_player_message(interaction.guild.id, interaction.channel.id, player.player_message.id)

    @app_commands.command(name="refresh", description="Refresh the player to show current progress")
    async def refresh(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)

        if not player.current:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        if not player.player_message:
            return await interaction.response.send_message("No player to refresh. Use /nowplaying instead.", ephemeral=True)

        # Update the existing player message
        embed = self.create_now_playing_embed(player)
        view = PlayerView(self, interaction.guild.id, player.is_paused)
        try:
            await player.player_message.edit(embed=embed, view=view)
            await interaction.response.send_message("🔄 Player refreshed!", ephemeral=True)
        except:
            await interaction.response.send_message("Couldn't refresh player. Use /nowplaying instead.", ephemeral=True)

    @app_commands.command(name="volume", description="Set the volume (0-100)")
    @app_commands.describe(level="Volume level from 0 to 100")
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]):
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I'm not in a voice channel!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        player.volume = level / 100

        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = player.volume

        await interaction.response.send_message(f"Volume set to **{level}%**")

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)

        if len(player.queue) < 2:
            return await interaction.response.send_message("Not enough songs in queue to shuffle!", ephemeral=True)

        queue_list = list(player.queue)
        random.shuffle(queue_list)
        player.queue = deque(queue_list)
        await interaction.response.send_message("Queue shuffled!")

    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode: off, one (current song), or all (entire queue)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Current Song", value="one"),
        app_commands.Choice(name="Entire Queue", value="all"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str] = None):
        player = self.get_player(interaction.guild.id)

        if mode is None:
            modes = ["off", "one", "all"]
            current_index = modes.index(player.loop_mode)
            player.loop_mode = modes[(current_index + 1) % 3]
        else:
            player.loop_mode = mode.value

        icons = {"off": "->", "one": "(1)", "all": "(all)"}
        await interaction.response.send_message(f"Loop mode: **{player.loop_mode}** {icons[player.loop_mode]}")

    @app_commands.command(name="seek", description="Seek to a position in the current song")
    @app_commands.describe(timestamp="Position to seek to (format: MM:SS or seconds)")
    async def seek(self, interaction: discord.Interaction, timestamp: str):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        player = self.get_player(interaction.guild.id)
        if not player.current:
            return await interaction.response.send_message("Nothing is playing!", ephemeral=True)

        try:
            if ":" in timestamp:
                parts = timestamp.split(":")
                if len(parts) == 2:
                    seconds = int(parts[0]) * 60 + int(parts[1])
                else:
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                seconds = int(timestamp)
        except ValueError:
            return await interaction.response.send_message("Invalid timestamp! Use MM:SS or seconds.", ephemeral=True)

        if seconds < 0 or seconds > player.current.duration:
            return await interaction.response.send_message(
                f"Timestamp must be between 0 and {Song.format_duration(player.current.duration)}",
                ephemeral=True
            )

        await interaction.response.defer()

        player.skip_next_callback = True
        vc.stop()

        ffmpeg_options = {
            'before_options': f'-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -ss {seconds}',
            'options': '-vn -sn -dn',
            'stderr': subprocess.DEVNULL,
        }

        source = discord.FFmpegPCMAudio(player.current.source, executable=FFMPEG_PATH, **ffmpeg_options)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)

        player.start_time = time.time() - seconds

        def after_seek(error):
            self.play_next(interaction.guild.id, vc, interaction.channel, error=error)

        vc.play(source, after=after_seek)
        await interaction.followup.send(f"Seeked to **{Song.format_duration(seconds)}**")

    @app_commands.command(name="remove", description="Remove a song from the queue")
    @app_commands.describe(position="Position of the song in the queue (1, 2, 3, etc.)")
    async def remove(self, interaction: discord.Interaction, position: int):
        player = self.get_player(interaction.guild.id)

        if not player.queue:
            return await interaction.response.send_message("The queue is empty!", ephemeral=True)

        if not 1 <= position <= len(player.queue):
            return await interaction.response.send_message(
                f"Invalid position! Use a number between 1 and {len(player.queue)}",
                ephemeral=True
            )

        queue_list = list(player.queue)
        removed = queue_list.pop(position - 1)
        player.queue = deque(queue_list)
        await interaction.response.send_message(f"Removed **{removed.title}** from the queue.")

    @app_commands.command(name="clear", description="Clear the queue")
    async def clear(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)
        player.queue.clear()
        await interaction.response.send_message("Queue cleared!")

    @app_commands.command(name="help", description="Show all available commands")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Music Bot Commands",
            description="Here are all available commands:",
            color=discord.Color.blue()
        )

        # Playback Controls
        embed.add_field(
            name="🎵 Playback",
            value=(
                "`/play <query>` - Play a song from YouTube\n"
                "`/pause` - Pause the current song\n"
                "`/resume` - Resume the paused song\n"
                "`/stop` - Stop playing and clear the queue\n"
                "`/skip` - Skip to the next song\n"
                "`/previous` - Go back to the previous song\n"
                "`/restart` - Restart the current song\n"
                "`/seek <timestamp>` - Seek to a position (MM:SS or seconds)"
            ),
            inline=False
        )

        # Queue Management
        embed.add_field(
            name="📋 Queue",
            value=(
                "`/queue` - Show the current queue\n"
                "`/nowplaying` - Show the currently playing song\n"
                "`/refresh` - Refresh the player to show current progress\n"
                "`/shuffle` - Shuffle the queue\n"
                "`/remove <position>` - Remove a song from queue\n"
                "`/clear` - Clear the entire queue\n"
                "`/loop <mode>` - Set loop mode (off/one/all)"
            ),
            inline=False
        )

        # Voice & Settings
        embed.add_field(
            name="🔊 Voice & Settings",
            value=(
                "`/join` - Join your voice channel\n"
                "`/leave` - Leave the voice channel\n"
                "`/volume <0-100>` - Set the volume"
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Handle voice state changes for auto-disconnect and forced disconnection"""
        # Check if the bot itself was disconnected
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                # Bot was disconnected from voice channel
                guild_id = member.guild.id
                player = self.players.get(guild_id)
                if player and player.text_channel:
                    try:
                        await player.text_channel.send("👋 Disconnected from voice channel.")
                    except:
                        pass
                # Clean up player state
                await self.cleanup_player(guild_id)
                if guild_id in self.players:
                    del self.players[guild_id]
                self.cancel_disconnect_timer(guild_id)
            return

        voice_client = member.guild.voice_client
        if not voice_client or not voice_client.channel:
            return

        # Check if someone left the bot's channel
        if before.channel == voice_client.channel and after.channel != voice_client.channel:
            # Count non-bot members in the channel
            members = [m for m in voice_client.channel.members if not m.bot]
            if len(members) == 0:
                # Bot is alone, start disconnect timer
                await self.start_disconnect_timer(member.guild.id, voice_client)

        # Cancel disconnect timer if someone joins
        if after.channel == voice_client.channel and before.channel != voice_client.channel:
            self.cancel_disconnect_timer(member.guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
