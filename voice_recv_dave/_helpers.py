from __future__ import annotations

import asyncio
import logging
import threading

from discord.ext.voice_recv.router import PacketRouter

log = logging.getLogger("voice_recv_dave")


def router_threads(vc) -> list:
    return [t for t in threading.enumerate()
            if isinstance(t, PacketRouter) and getattr(getattr(t, "reader", None), "voice_client", None) is vc]


async def release_reader(vc, timeout: float = 2.0) -> None:
    if getattr(vc, "_reader", None):
        vc.stop_listening()
    waited = 0.0
    while True:
        alive = router_threads(vc)
        if not alive:
            return
        for thread in alive:
            reader = thread.reader
            if reader is getattr(vc, "_reader", None):
                if reader.is_listening():
                    vc.stop_listening()
            elif reader.is_listening():
                reader.stop()
        if waited >= timeout:
            log.warning("%d old packet router(s) still running after %.1fs; the next listen() may race",
                        len(alive), timeout)
            return
        await asyncio.sleep(0.02)
        waited += 0.02


def drain_stale_audio(vc) -> int:
    sock = getattr(getattr(vc, "_connection", None), "socket", None)
    if not sock:
        return 0
    dropped = 0
    try:
        while dropped < 10_000:
            sock.recv(4096)
            dropped += 1
    except (BlockingIOError, InterruptedError):
        pass
    except OSError as e:
        log.debug("drain_stale_audio: %s", e)
    if dropped:
        log.info("dropped %d stale voice packet(s) that queued up before listening", dropped)
    return dropped


async def listen(vc, sink, *, after=None, timeout: float = 2.0) -> None:
    await release_reader(vc, timeout)
    drain_stale_audio(vc)
    vc.listen(sink, after=after)
