import asyncio
import logging
import os

import discord
from discord.ext import commands, voice_recv

import voice_recv_dave

logging.basicConfig(level=logging.INFO)
voice_recv_dave.install()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
recordings: dict[int, tuple[voice_recv_dave.TimeMixSink, str]] = {}


@bot.command()
async def join(ctx: commands.Context):
    if ctx.author.voice is None:
        return await ctx.send("Join a voice channel first.")
    await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
    await ctx.send("Joined. Use `!record` to start.")


@bot.command()
async def record(ctx: commands.Context):
    if ctx.voice_client is None:
        return await ctx.send("Use `!join` first.")
    path = f"recording-{ctx.guild.id}.wav"
    sink = voice_recv_dave.TimeMixSink(path)
    recordings[ctx.guild.id] = (sink, path)
    await voice_recv_dave.listen(ctx.voice_client, sink)
    await ctx.send("Recording. Use `!stop` to finish.")


@bot.command()
async def stop(ctx: commands.Context):
    entry = recordings.pop(ctx.guild.id, None)
    if ctx.voice_client is None or entry is None:
        return await ctx.send("Not recording.")
    sink, path = entry
    ctx.voice_client.stop_listening()
    await asyncio.to_thread(sink.closed.wait, 5)
    stats = sink.stats()
    await ctx.send(f"{stats['seconds']:.1f}s recorded, {stats['voiced_seconds']:.1f}s of speech.\n"
                   f"`{voice_recv_dave.summary()}`", file=discord.File(path))


@bot.command()
async def leave(ctx: commands.Context):
    if ctx.voice_client is not None:
        await ctx.voice_client.disconnect()


bot.run(os.environ["DISCORD_TOKEN"])
