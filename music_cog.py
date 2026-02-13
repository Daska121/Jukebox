import asyncio
from typing import Dict, List, Optional
import discord
from discord.ext import commands
import yt_dlp
from datetime import datetime

MUSICBOX_RED = 0xE53935  
HELP_THUMBNAIL_URL = "https://static.wikia.nocookie.net/minecraft_gamepedia/images/e/ee/Jukebox_JE2_BE2.png/revision/latest?cb=20201202075007"  
HELP_BANNER_URL = "https://imgur.com/GA98FeQ.png"     


def make_embed(title: str, description: str = "", *, color: int = 0x2F3136) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
    embed.set_footer(text="Music Box")
    return embed


YTDL_OPTIONS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": True,
    "quiet": False,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "js_runtimes": {"deno": {"path": r"C:\Users\username\.deno\bin\deno.exe"}}, #Replace with your Username
    "remote_components": ["ejs:github"],
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "referer": "https://www.youtube.com/",
    "cookiefile": "cookies.txt",
}


ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def format_duration(seconds: Optional[int]) -> str:
    """Turn seconds into something like 3:42 or 1:05:10."""
    if not seconds:
        return "?"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


class music_cog(commands.Cog):
    """
    A simple music cog:
    - Keeps a queue PER SERVER (guild)
    - Joins the voice channel where the user is
    - Plays YouTube audio (URL or search)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # queue[guild_id] = list of tracks waiting to play
        self.queue: Dict[int, List[dict]] = {}

        # now_playing[guild_id] = current track info (dict) or None
        self.now_playing: Dict[int, Optional[dict]] = {}

        # lock per guild so we don't start 2 songs at the same time accidentally
        self.play_lock: Dict[int, asyncio.Lock] = {}

        # guild_id -> asyncio.Task
        self.idle_tasks = {}

        # 5 minutes
        self.IDLE_SECONDS = 300

    # -------------------- small helpers --------------------

    def get_guild_id(self, ctx: commands.Context) -> int:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("Use music commands inside a server, not DMs.")
        return ctx.guild.id

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.play_lock:
            self.play_lock[guild_id] = asyncio.Lock()
        return self.play_lock[guild_id]

    async def ensure_bot_in_voice(self, ctx: commands.Context) -> discord.VoiceClient:
        """
        Make sure the bot is connected to the user's voice channel.
        If the bot is already connected somewhere else, move it.
        """
        if not isinstance(ctx.author, discord.Member) or not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError("You must join a voice channel first.")

        user_channel = ctx.author.voice.channel
        voice_client = ctx.voice_client

        # If bot is already connected, just move if needed
        if voice_client and voice_client.is_connected():
            if voice_client.channel != user_channel:
                await voice_client.move_to(user_channel)
            return voice_client

        # Otherwise connect
        return await user_channel.connect()

    async def get_track_info(self, query: str) -> dict:
        """
        Use yt-dlp to find a playable stream URL.
        query can be a YouTube URL or a search like "coldplay paradise".
        """
        def extract():
            info = ytdl.extract_info(query, download=False)

            # If it's a search, yt-dlp gives "entries"
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            return {
                "thumbnail": info.get("thumbnail"),
                "title": info.get("title", "Unknown title"),
                "webpage_url": info.get("webpage_url", query),
                "stream_url": info["url"],
                "duration": info.get("duration"),
            }


        return await asyncio.to_thread(extract)

    async def play_next_song(self, ctx: commands.Context):

        guild_id = self.get_guild_id(ctx)
        lock = self.get_lock(guild_id)

        async with lock:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return

            songs = self.queue.get(guild_id, [])

            if not songs:
                self.now_playing[guild_id] = None
                self._start_idle_timer(ctx)
                return

            track = songs.pop(0)
            self.now_playing[guild_id] = track


            def after_playing(error):
                if error:
                    print("PLAYER ERROR:", repr(error))
                asyncio.run_coroutine_threadsafe(self.play_next_song(ctx), self.bot.loop)

            try:

                source = discord.FFmpegPCMAudio(
                    track["stream_url"],
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    options="-vn",
                )

                vc.play(source, after=after_playing)

            except Exception as e:
                print("PLAY START ERROR:", repr(e))
                await ctx.send(f"❌ Could not start playback: `{type(e).__name__}: {e}`")
                return

            embed = make_embed(
                "▶️ Now Playing",
                f"**{track['title']}** (`{format_duration(track.get('duration'))}`)",
                color=0x00FF1D
            )
            embed.add_field(name="Link", value=track["webpage_url"], inline=False)
            await ctx.send(embed=embed)

            print(f"---> Now playing: **{track['title']}**")

    def _cancel_idle_timer(self, guild_id: int):
        task = self.idle_tasks.get(guild_id)
        if task and not task.done():
            task.cancel()
        self.idle_tasks[guild_id] = None

    def _start_idle_timer(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)

        # Cancel any existing timer first
        self._cancel_idle_timer(guild_id)

        async def _idle_disconnect():
            try:
                await asyncio.sleep(self.IDLE_SECONDS)

                vc = ctx.voice_client
                if not vc or not vc.is_connected():
                    return

                # Only leave if still idle
                queue_empty = len(self.queue.get(guild_id, [])) == 0
                nothing_playing = (not vc.is_playing()) and (not vc.is_paused())

                if queue_empty and nothing_playing:
                    await vc.disconnect()
                    self.now_playing[guild_id] = None
                    self.queue[guild_id] = []

                    # Optional: send a message to the last used text channel
                    await ctx.send(
                        embed=discord.Embed(
                            title="💤 Auto Disconnect",
                            description="No activity for 5 minutes, leaving the voice channel.",
                            color=0xE53935
                        )
                    )
            except asyncio.CancelledError:
                pass

        self.idle_tasks[guild_id] = asyncio.create_task(_idle_disconnect())

    # -------------------- commands --------------------

    @commands.command(name="join", aliases=["j"])
    async def join(self, ctx: commands.Context):
        vc = await self.ensure_bot_in_voice(ctx)

        self._cancel_idle_timer(guild_id)
        embed = make_embed(
            "✅ Joined Voice Channel",
            f"I joined **{vc.channel}**",
            color=0x57F287
        )
        await ctx.send(embed=embed)

    @commands.command(name="leave", aliases=["dc", "disconnect", "l"])
    async def leave(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)

        # clear state
        self.queue[guild_id] = []
        self.now_playing[guild_id] = None

        vc = ctx.voice_client
        if vc and vc.is_connected():
            channel_name = str(vc.channel)
            await vc.disconnect()
            await ctx.send(embed=make_embed("👋 Disconnected", f"Left **{channel_name}**", color=0xED4245))
        else:
            await ctx.send(embed=make_embed("ℹ️ Not Connected", "I’m not in a voice channel.", color=0xED4245))

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        # ✅ IMPORTANT: get the actual voice client object from the connect/move function
        vc = await self.ensure_bot_in_voice(ctx)

        guild_id = self.get_guild_id(ctx)

        self._cancel_idle_timer(guild_id)

        if not query.startswith("http"):
            query = f"ytsearch1:{query}"

        track = await self.get_track_info(query)

        self.queue.setdefault(guild_id, []).append(track)

        embed = make_embed(
            "➕ Added to Queue",
            f"**{track['title']}** (`{format_duration(track.get('duration'))}`)",
            color=0x5865F2

        )
        embed.add_field(name="Source", value=track["webpage_url"], inline=False)
        if track.get("thumbnail"):
            embed.set_thumbnail(url=track["thumbnail"])

        await ctx.send(embed=embed)

        # ✅ Start immediately if idle (use vc we already have)
        if not vc.is_playing() and not vc.is_paused():
            await self.play_next_song(ctx)

    @commands.command(name="skip", aliases=["s", "next"])
    async def skip(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send(embed=make_embed("ℹ️ Not Connected", "I’m not in a voice channel.", color=0xED4245))

        if not vc.is_playing() and not vc.is_paused():
            return await ctx.send(embed=make_embed("ℹ️ Nothing Playing", "There’s nothing to skip.", color=0xED4245))

        vc.stop()  # triggers after_playing -> play_next_song
        await ctx.send(embed=make_embed("⏭️ Skipped", "Moving to the next track…", color=0xFEE75C))

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send(embed=make_embed("ℹ️ Not Connected", "I’m not in a voice channel.", color=0xED4245))

        if vc.is_playing():
            vc.pause()
            await ctx.send(embed=make_embed("⏸️ Paused", "Playback paused.", color=0x5865F2))
        else:
            await ctx.send(embed=make_embed("ℹ️ Nothing Playing", "Nothing is currently playing.", color=0xED4245))

    @commands.command(name="resume", aliases=["r"])
    async def resume(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send(embed=make_embed("ℹ️ Not Connected", "I’m not in a voice channel.", color=0xED4245))

        if vc.is_paused():
            vc.resume()
            await ctx.send(embed=make_embed("▶️ Resumed", "Playback resumed.", color=0x57F287))
        else:
            await ctx.send(embed=make_embed("ℹ️ Not Paused", "Playback isn’t paused.", color=0xED4245))

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)
        vc = ctx.voice_client
        self._start_idle_timer(ctx)

        # clear queue + now playing
        self.queue[guild_id] = []
        self.now_playing[guild_id] = None

        if not vc or not vc.is_connected():
            return await ctx.send(embed=make_embed("ℹ️ Not Connected", "I’m not in a voice channel.", color=0xED4245))

        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await ctx.send(embed=make_embed("⏹️ Stopped", "Stopped playback and cleared the queue.", color=0xED4245))
        else:
            await ctx.send(embed=make_embed("ℹ️ Nothing Playing", "Nothing is currently playing.", color=0xED4245))

    @commands.command(name="queue", aliases=["q"])
    async def show_queue(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)
        songs = self.queue.get(guild_id, [])

        if not songs:
            return await ctx.send(embed=make_embed("🎶 Queue", "Queue is empty.", color=0xED4245))

        lines = []
        for i, t in enumerate(songs[:10], start=1):
            lines.append(f"**{i}.** {t['title']} (`{format_duration(t.get('duration'))}`)")

        embed = make_embed("🎶 Queue", "\n".join(lines), color=0x5865F2)
        if len(songs) > 10:
            embed.set_footer(text=f"…and {len(songs) - 10} more | Music Box")
        await ctx.send(embed=embed)

    @commands.command(name="np", aliases=["nowplaying"])
    async def nowplaying(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)
        track = self.now_playing.get(guild_id)

        if not track:
            return await ctx.send(embed=make_embed("🎧 Now Playing", "Nothing is playing.", color=0xED4245))

        embed = make_embed(
            "🎧 Now Playing",
            f"**{track['title']}** (`{format_duration(track.get('duration'))}`)",
            color=0x00FF1D
        )
        embed.add_field(name="Link", value=track["webpage_url"], inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context):
        prefix = ctx.clean_prefix

        embed = discord.Embed(
            title="🎵 Music Box — Help",
            description="Here are my commands:",
            color=MUSICBOX_RED
        )

        embed.add_field(
            name="🎧 Voice",
            value=(
                f"`{prefix}join` / `{prefix}j` — Join your voice channel\n"
                f"`{prefix}leave` / `{prefix}l` — Leave and clear the queue"
            ),
            inline=False
        )

        embed.add_field(
            name="🔥 Music",
            value=(
                f"`{prefix}play <url or search>` / `{prefix}p` — Play or queue a song\n"
                f"`{prefix}skip` / `{prefix}s` — Skip current song\n"
                f"`{prefix}pause` — Pause playback\n"
                f"`{prefix}resume` / `{prefix}r` — Resume playback\n"
                f"`{prefix}stop` — Stop and clear the queue"
            ),
            inline=False
        )

        embed.add_field(
            name="📜 Info",
            value=(
                f"`{prefix}queue` / `{prefix}q` — Show the queue\n"
                f"`{prefix}np` — Now playing"
            ),
            inline=False
        )

        embed.add_field(
            name="✨ Examples",
            value=(
                f"`{prefix}play arial math`\n"
                f"`{prefix}play https://www.youtube.com/watch?v=atgjKEgSqSU`\n"
                f"`{prefix}queue`"
            ),
            inline=False
        )

        # Optional visuals
        embed.set_thumbnail(url=HELP_THUMBNAIL_URL)
        embed.set_image(url=HELP_BANNER_URL)

        embed.set_footer(text="Music Box • YouTube audio")
        await ctx.send(embed=embed)


# IMPORTANT for discord.py 2.x extensions
async def setup(bot: commands.Bot):
    await bot.add_cog(music_cog(bot))

