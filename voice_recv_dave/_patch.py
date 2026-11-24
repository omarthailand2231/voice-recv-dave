from __future__ import annotations

import logging
import threading
import weakref

log = logging.getLogger("voice_recv_dave")

_COUNTERS = ("ok", "fail", "nouser", "bypass", "passthru", "reheal", "padded")
_readers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_decode_errors = {"n": 0}
_install_lock = threading.Lock()


def _should_log(n: int) -> bool:
    return n in (1, 25, 250) or n % 1000 == 0


def summary() -> dict:
    total = {name: 0 for name in _COUNTERS}
    for state in list(_readers.values()):
        for name in _COUNTERS:
            total[name] += int(state.get(name, 0))
    total["readers"] = len(_readers)
    total["undecodable"] = _decode_errors["n"]
    total["e2ee"] = total["ok"] + total["fail"] > 0
    return total


def strip_rtp_padding(data: bytes) -> bytes:
    if not data:
        return data
    count = data[-1]
    if 0 < count <= len(data):
        return data[:-count]
    return data


def _resync_key(reader, vc, state: dict) -> None:
    try:
        key = getattr(vc, "secret_key", None)
        if not key:
            return
        key = bytes(key)
        if key == state["key"]:
            state["xerr"] += 1
            if _should_log(state["xerr"]):
                log.warning("transport decrypt failed %d time(s) with the current key; "
                            "waiting for Discord to send a new one", state["xerr"])
            return
        reader.update_secret_key(key)
        state["key"] = key
        state["xerr"] = 0
        state["reheal"] += 1
        log.info("receive key re-synced from the voice client (#%d); no rejoin needed", state["reheal"])
    except Exception as e:
        log.debug("key re-sync failed: %s", e)


def _wrap(reader, davey, silence: bytes, crypto_error) -> None:
    vc = reader.voice_client
    transport_decrypt = reader.decryptor.decrypt_rtp
    key = getattr(vc, "secret_key", None)
    state = {name: 0 for name in _COUNTERS}
    state.update(xerr=0, announced=False, key=bytes(key) if key else None)
    audio = davey.MediaType.audio

    def decrypt_rtp(packet):
        try:
            data = transport_decrypt(packet)
        except crypto_error:
            _resync_key(reader, vc, state)
            return silence
        state["xerr"] = 0

        if getattr(packet, "padding", False) and data:
            data = strip_rtp_padding(data)
            state["padded"] += 1
            if not data:
                return silence

        conn = getattr(vc, "_connection", None)
        session = getattr(conn, "dave_session", None)
        if session is None or not getattr(conn, "dave_protocol_version", 0) or not session.ready:
            state["bypass"] += 1
            return data
        if data == silence:
            return data

        if not state["announced"]:
            state["announced"] = True
            log.info("end-to-end encrypted call (DAVE v%s): decrypting received audio",
                     getattr(conn, "dave_protocol_version", "?"))

        user_id = vc._get_id_from_ssrc(packet.ssrc)
        if user_id is None:
            state["nouser"] += 1
            return silence

        try:
            out = session.decrypt(user_id, audio, bytes(data))
        except Exception as e:
            text = str(e)
            if "Passthrough" in text or "Unencrypted" in text:
                state["passthru"] += 1
                return data
            state["fail"] += 1
            if _should_log(state["fail"]):
                log.warning("DAVE decrypt failed (%d failed, %d ok): %s", state["fail"], state["ok"], e)
            return silence

        state["ok"] += 1
        if state["ok"] == 1:
            log.info("first DAVE frame decrypted (user %s)", user_id)
        return out

    reader.decryptor.decrypt_rtp = decrypt_rtp
    _readers[reader] = state


def _install_decrypt() -> bool:
    try:
        import davey
    except ImportError:
        log.info("davey is not installed, so this bot can't join end-to-end encrypted calls; "
                 "the DAVE decrypt layer was not installed")
        return False

    from discord.ext.voice_recv import reader as reader_module
    from discord.ext.voice_recv.rtp import OPUS_SILENCE
    from nacl.exceptions import CryptoError

    cls = reader_module.AudioReader
    if getattr(cls, "_voice_recv_dave", False):
        return True
    original_init = cls.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            _wrap(self, davey, OPUS_SILENCE, CryptoError)
        except Exception as e:
            log.error("could not attach DAVE decryption to this reader: %s", e)

    cls.__init__ = __init__
    cls._voice_recv_dave = True
    return True


def _install_decode_guard() -> None:
    from discord.ext.voice_recv.opus import PacketDecoder
    from discord.opus import Decoder, OpusError

    if getattr(PacketDecoder, "_voice_recv_dave_guard", False):
        return
    original = PacketDecoder._decode_packet
    silence = b"\x00" * Decoder.FRAME_SIZE

    def _decode_packet(self, packet):
        try:
            return original(self, packet)
        except OpusError as e:
            n = _decode_errors["n"] = _decode_errors["n"] + 1
            if _should_log(n):
                log.warning("undecodable opus frame #%d (ssrc=%s): %s; replaced with 20 ms of silence",
                            n, getattr(self, "ssrc", "?"), e)
            return packet, silence

    PacketDecoder._decode_packet = _decode_packet
    PacketDecoder._voice_recv_dave_guard = True


def install(*, decode_guard: bool = True) -> bool:
    with _install_lock:
        if decode_guard:
            _install_decode_guard()
        installed = _install_decrypt()
    if installed:
        import davey
        from discord.ext import voice_recv
        log.info("installed (davey %s, voice_recv %s)", getattr(davey, "__version__", "?"),
                 getattr(voice_recv, "__version__", "?"))
    return installed
