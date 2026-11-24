from __future__ import annotations

import struct

import nacl.secret
from discord.ext.voice_recv.rtp import decode_rtp


class FakeConnection:
    def __init__(self, session=None, version: int = 1) -> None:
        self.dave_session = session
        self.dave_protocol_version = version
        self.socket = None
        self.listeners: list = []

    def add_socket_listener(self, callback) -> None:
        self.listeners.append(callback)

    def remove_socket_listener(self, callback) -> None:
        if callback in self.listeners:
            self.listeners.remove(callback)


class FakeVoiceClient:
    mode = "aead_xchacha20_poly1305_rtpsize"

    def __init__(self, key: bytes, session=None, *, version: int = 1, users: dict | None = None) -> None:
        self.secret_key = list(key)
        self._connection = FakeConnection(session, version)
        self._users = dict(users or {})
        self._reader = None
        self.guild = None
        self.listened: list = []

    def _get_id_from_ssrc(self, ssrc: int):
        return self._users.get(ssrc)

    def stop_listening(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_listening():
            reader.stop()

    def listen(self, sink, *, after=None) -> None:
        self.listened.append((sink, after))


class FakeSession:
    ready = True

    def __init__(self) -> None:
        self.seen: list = []

    def decrypt(self, user_id, media_type, data: bytes) -> bytes:
        self.seen.append((user_id, data))
        if data.startswith(b"PLAIN"):
            raise ValueError("Failed to decrypt: UnencryptedWhenPassthroughDisabled")
        if data.startswith(b"BROKEN"):
            raise ValueError("Failed to decrypt: DecryptionFailed(NoValidCryptorFound)")
        return b"OPUS" + data[len(b"DAVE"):]


def rtp_packet(key: bytes, payload: bytes, *, ssrc: int = 7, seq: int = 1, padding: int = 0):
    first = 0x80 | (0x20 if padding else 0)
    header = struct.pack(">BBHII", first, 0x78, seq, seq * 960, ssrc)
    body = payload + (bytes(padding - 1) + bytes([padding]) if padding else b"")
    nonce = struct.pack(">I", seq) + bytes(20)
    sealed = nacl.secret.Aead(bytes(key)).encrypt(body, header, nonce).ciphertext
    return decode_rtp(header + sealed + nonce[:4])
