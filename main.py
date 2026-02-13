import asyncio
import discord
from music_cog import music_cog
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True   # REQUIRED for commands like !join
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
