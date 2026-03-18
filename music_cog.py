"""
music_cog.py — Music commands for the Jukebox Discord bot.

Audio pipeline
--------------
For direct HTTPS streams (webm/m4a):
    yt-dlp (Python API) → stream URL → FFmpegOpusAudio → FilteredOpusAudio → DAVE encrypt → Discord

For HLS streams (m3u8, the default for YouTube in 2026):
    yt-dlp subprocess (--source-address 0.0.0.0, pipes to stdout)
        → FFmpegOpusAudio(pipe=True)
        → FilteredOpusAudio
        → DAVE encrypt
        → Discord

The yt-dlp subprocess forces IPv4 (`--source-address 0.0.0.0`) to match the IP
that was used during metadata extraction; YouTube's HLS segment URLs are signed
with a specific IP and return 403 if the request comes from a different address
(e.g. IPv6 on a dual-stack Windows host).

To minimise the silence gap between songs, the next song's yt-dlp subprocess is
pre-started while the current song is still playing.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

import discord
from discord.ext import commands
import yt_dlp
from datetime import datetime

_BOT_DIR = os.path.dirname(os.path.abspath(__file__))

MUSICBOX_RED = 0xE53935
HELP_THUMBNAIL_URL = "https://static.wikia.nocookie.net/minecraft_gamepedia/images/e/ee/Jukebox_JE2_BE2.png/revision/latest?cb=20201202075007"
HELP_BANNER_URL = "https://imgur.com/GA98FeQ.png"


def make_embed(title: str, description: str = "", *, color: int = 0x2F3136) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
    embed.set_footer(text="Jukebox")
    return embed

# yt-dlp settings: we want audio only, no playlists, and allow "ytsearch:" queries
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": False,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "cookiefile": "cookies.txt",
    "remote_components": ["ejs:github"],
}


ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class FilteredOpusAudio(discord.AudioSource):
    """Wraps FFmpegOpusAudio and drops Ogg container header packets (OpusHead / OpusTags)
    so only real Opus audio frames reach Discord."""
    _SKIP_PREFIXES = (b'OpusHead', b'OpusTags')

    def __init__(self, src: discord.FFmpegOpusAudio, *, proc=None) -> None:
        self._src = src
        self._proc = proc

    def read(self) -> bytes:
        while True:
            data = self._src.read()
            if not data or data[:8] not in self._SKIP_PREFIXES:
                return data

    def is_opus(self) -> bool:
        return True

    def cleanup(self) -> None:
        self._src.cleanup()
        if self._proc is not None:
            _cleanup_proc(self._proc)


def format_duration(seconds: Optional[int]) -> str:
    """Turn seconds into something like 3:42 or 1:05:10."""
    if not seconds:
        return "?"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _cleanup_proc(proc: subprocess.Popen) -> None:
    """Kill a yt-dlp subprocess and remove its isolated temp directory."""
    try:
        proc.kill()
    except Exception:
        pass
    tmp_dir = getattr(proc, '_tmp_dir', None)
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _start_hls_proc(stream_url: str) -> subprocess.Popen:
    """Start a yt-dlp subprocess that pipes HLS audio to stdout.

    Each subprocess gets its own temp directory so fragment files from
    different songs (or orphaned processes from previous runs) never collide.
    """
    tmp_dir = tempfile.mkdtemp(prefix='jukebox_')
    proc = subprocess.Popen(
        [
            sys.executable, '-m', 'yt_dlp',
            '--source-address', '0.0.0.0',
            '--cookies', os.path.join(_BOT_DIR, 'cookies.txt'),
            '--quiet', '--no-warnings',
            '-o', '-',
            stream_url,
        ],
        stdout=subprocess.PIPE,
        stderr=None,
        cwd=tmp_dir,          # fragment/part files stay in this isolated dir
    )
    proc._tmp_dir = tmp_dir   # remembered so cleanup() can delete it
    return proc


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

        # Guilds where WE explicitly connected to voice (not Discord's stale auto-reconnect)
        self._authorized_guilds: set = set()

    # -------------------- small helpers --------------------

    def _kill_queued_procs(self, guild_id: int) -> None:
        """Kill any pre-started download subprocesses sitting in the queue."""
        for track in self.queue.get(guild_id, []):
            proc = track.pop('yt_proc', None)
            if proc is not None:
                _cleanup_proc(proc)

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

        guild_id = ctx.guild.id
        user_channel = ctx.author.voice.channel
        voice_client = ctx.voice_client

        # Mark this guild as authorized BEFORE connecting so on_voice_state_update
        # knows this is a legitimate join (not a stale auto-reconnect).
        self._authorized_guilds.add(guild_id)

        # If bot is already connected, just move if needed
        if voice_client and voice_client.is_connected():
            if voice_client.channel != user_channel:
                await voice_client.move_to(user_channel)
            return voice_client

        # Clean up any stale (disconnected) voice client before reconnecting
        if voice_client and not voice_client.is_connected():
            await voice_client.disconnect(force=True)

        # Connect once — no auto-reconnect loop on failure
        try:
            return await user_channel.connect(reconnect=False)
        except asyncio.TimeoutError:
            self._authorized_guilds.discard(guild_id)
            raise commands.CommandError("⏱️ Could not connect to the voice channel (timed out). Check your network or try again.")
        except discord.ConnectionClosed as e:
            self._authorized_guilds.discard(guild_id)
            raise commands.CommandError(f"🔌 Voice connection closed by Discord (code {e.code}). Try updating discord.py: `pip install -U discord.py`")

    async def get_track_info(self, query: str) -> dict:

        def extract():
            info = ytdl.extract_info(query, download=False)

            # If it's a search, yt-dlp gives "entries"
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            stream_url = info["url"]

            return {
                "thumbnail": info.get("thumbnail"),
                "title": info.get("title", "Unknown title"),
                "webpage_url": info.get("webpage_url", query),
                "stream_url": stream_url,
                "protocol": info.get("protocol", ""),
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
                # Use pre-started subprocess if available (started by previous song),
                # otherwise start one now.
                yt_proc = track.pop('yt_proc', None)
                protocol = track.get('protocol', '')
                if protocol.startswith('m3u8'):
                    if yt_proc is None:
                        yt_proc = _start_hls_proc(track['stream_url'])
                    _raw = discord.FFmpegOpusAudio(yt_proc.stdout, pipe=True, bitrate=128)
                    source = FilteredOpusAudio(_raw, proc=yt_proc)
                else:
                    if yt_proc is not None:
                        yt_proc.kill()  # shouldn't happen, but clean up
                    _raw = discord.FFmpegOpusAudio(
                        track['stream_url'],
                        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        bitrate=128,
                    )
                    source = FilteredOpusAudio(_raw)
                vc.play(source, after=after_playing)

                # Pre-start the next HLS download while current song plays
                next_songs = self.queue.get(guild_id, [])
                if next_songs:
                    nxt = next_songs[0]
                    if nxt.get('protocol', '').startswith('m3u8') and 'yt_proc' not in nxt:
                        nxt['yt_proc'] = _start_hls_proc(nxt['stream_url'])
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
                    self._authorized_guilds.discard(guild_id)
                    await vc.disconnect()
                    self.now_playing[guild_id] = None
                    self._kill_queued_procs(guild_id)
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

    # -------------------- listeners --------------------

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # Unwrap CheckFailure wrappers so the real cause is visible
        if isinstance(error, commands.CommandInvokeError):
            error = error.original
        print(f"[ERROR] Command '{ctx.command}' raised: {type(error).__name__}: {error}")
        await ctx.send(embed=make_embed("❌ Error", str(error), color=MUSICBOX_RED))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member != self.bot.user:
            return
        if before.channel is None and after.channel is not None:
            print(f"[VOICE] Joined: {after.channel} (guild: {member.guild})")
            # Discord replays cached voice state on reconnect, causing an auto-join loop.
            if member.guild.id not in self._authorized_guilds:
                print(f"[VOICE] Unauthorized auto-join — leaving immediately")
                await member.guild.change_voice_state(channel=None)
                if member.guild.voice_client:
                    await member.guild.voice_client.disconnect(force=True)
        elif before.channel is not None and after.channel is None:
            print(f"[VOICE] Left: {before.channel} (guild: {member.guild})")
            guild_id = member.guild.id
            self._kill_queued_procs(guild_id)
            self.now_playing.pop(guild_id, None)
            self._cancel_idle_timer(guild_id)
        elif before.channel != after.channel:
            print(f"[VOICE] Moved: {before.channel} -> {after.channel} (guild: {member.guild})")

    # -------------------- commands --------------------

    @commands.command(name="join", aliases=["j"])
    async def join(self, ctx: commands.Context):
        guild_id = self.get_guild_id(ctx)

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
        self._authorized_guilds.discard(guild_id)
        self._kill_queued_procs(guild_id)
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

        # yt-dlp extraction is slow (~15s). During that time the startup cleanup may have
        # disconnected the stale voice client we got earlier. Refresh the vc reference now.
        current_vc = ctx.voice_client
        if not current_vc or not current_vc.is_connected():
            try:
                current_vc = await self.ensure_bot_in_voice(ctx)
            except commands.CommandError:
                self.queue[guild_id].pop()
                await ctx.send(embed=make_embed("❌ Voice Lost", "Lost voice connection during lookup. Please try again.", color=MUSICBOX_RED))
                return

        if not current_vc.is_playing() and not current_vc.is_paused() and self.now_playing.get(guild_id) is None:
            # Pre-start the HLS subprocess now so it's already buffering when
            # play_next_song picks it up (reduces the initial startup lag).
            if track.get('protocol', '').startswith('m3u8') and 'yt_proc' not in track:
                track['yt_proc'] = _start_hls_proc(track['stream_url'])
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
        self._kill_queued_procs(guild_id)
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

    @commands.command(name="test")
    async def test_audio(self, ctx: commands.Context):
        """Play a synthetic 440 Hz sine wave for 5 seconds to verify audio works."""
        vc = await self.ensure_bot_in_voice(ctx)
        if vc.is_playing() or vc.is_paused():
            return await ctx.send(embed=make_embed("⚠️ Already Playing", "Stop or skip current track first.", color=0xED4245))
        _raw = discord.FFmpegOpusAudio(
            "sine=frequency=440:sample_rate=48000:duration=5",
            before_options="-f lavfi",
            bitrate=128,
        )
        source = FilteredOpusAudio(_raw)
        vc.play(source)
        await ctx.send(embed=make_embed("🔊 Audio Test", "Playing a 5-second 440 Hz test tone. Can you hear it?", color=0x5865F2))

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
