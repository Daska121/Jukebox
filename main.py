import asyncio
import os
import sys
import discord
import discord.voice_state
import discord.gateway

# Ensure the working directory is always the bot's folder so relative paths
# (token.txt, cookies.txt, etc.) resolve correctly regardless of how it's launched.
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Replace davey with dave.py (official Discord libdave) ───────────────────
# davey 0.1.4 (Snazzah's Rust impl) produces DAVE-encrypted frames that
# Discord's client cannot decrypt → silent audio.
# We swap it out for dave.py (DisnakeDev, official C++ libdave bindings).
import davey_compat
discord.voice_state.davey = davey_compat
discord.gateway.davey     = davey_compat
davey_compat.patch_reinit(discord.voice_state)   # inject _voice_state for channel-member lookup
# ────────────────────────────────────────────────────────────────────────────

from discord.ext import commands
from music_cog import music_cog

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

async def main():
    await bot.add_cog(music_cog(bot))

    with open("token.txt", "r") as file:
        token = file.readline().strip()

    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())