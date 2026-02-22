import os
import sys
import logging
import signal
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

load_dotenv()

# Set up logging to file only (no console output)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('bot.log', mode='w', encoding='utf-8')
    ]
)
logger = logging.getLogger('discord-bot')

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="?", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"{bot.user} is now online!")
    print(f"{bot.user} is now online!")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}", exc_info=True)
        print(f"Failed to sync commands: {e}")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/play"
    ))
    print("Bot ready!")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: {error.param.name}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Invalid argument provided.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")
        logger.error(f"Command error: {error}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    logger.error(f"Slash command error in /{interaction.command.name}: {error}", exc_info=True)
    if interaction.response.is_done():
        await interaction.followup.send(f"An error occurred: {str(error)}", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {str(error)}", ephemeral=True)


async def cleanup():
    """Clean up all player messages before shutdown"""
    logger.info("Cleaning up before shutdown...")
    music_cog = bot.get_cog('Music')
    if music_cog:
        for guild_id in list(music_cog.players.keys()):
            await music_cog.cleanup_player(guild_id)
    logger.info("Cleanup complete")
    print("Bot shutting down...")


async def main():
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        print("Received shutdown signal...")
        asyncio.create_task(shutdown())

    async def shutdown():
        await cleanup()
        await bot.close()

    # Only set signal handlers on Unix-like systems
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)

    async with bot:
        await bot.load_extension("cogs.music")
        try:
            await bot.start(os.getenv("DISCORD_TOKEN"))
        except asyncio.CancelledError:
            pass
        finally:
            await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
