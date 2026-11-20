"""Wire codec and clock-injected state machines. No sockets or sleeps here."""

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import math
import struct
import zlib

MAX_CHUNK = 512
MAX_CHUNKS = 128
MAX_BYTES = MAX_CHUNK * MAX_CHUNKS
HEADER = struct.Struct("!4sBB16sIIH")
CRC = struct.Struct("!I")
MAX_DATAGRAM = HEADER.size + MAX_CHUNK + CRC.size
MAGIC = b"LUDP"
VERSION = 1


class Kind(IntEnum):
    DATA = 1
    ACK = 2
    FIN = 3
    DONE = 4


@dataclass(frozen=True)
class Frame:
    kind: Kind
    session: bytes
    sequence: int
    total: int
    payload: bytes = b""


def validate(frame: Frame) -> None:
    if not isinstance(frame.kind, Kind):
        raise ValueError("Unknown frame kind")
    if not isinstance(frame.session, bytes) or len(frame.session) != 16:
        raise ValueError("Session must be 16 bytes")
    if not isinstance(frame.payload, bytes):
        raise ValueError("Payload must be bytes")
    if type(frame.total) is not int or not 0 <= frame.total <= MAX_CHUNKS:
        raise ValueError("Chunk count outside bounds")
    if type(frame.sequence) is not int:
        raise ValueError("Sequence must be an integer")
    if frame.kind in (Kind.DATA, Kind.ACK):
        if not 0 <= frame.sequence < frame.total:
            raise ValueError("Data/ACK sequence outside transfer")
        if frame.kind == Kind.DATA and not 1 <= len(frame.payload) <= MAX_CHUNK:
            raise ValueError("Data payload outside bounds")
        if frame.kind == Kind.ACK and frame.payload:
            raise ValueError("ACK payload must be empty")
    elif frame.sequence != frame.total or len(frame.payload) != 32:
        raise ValueError("FIN/DONE requires terminal sequence and SHA-256 digest")


def encode(frame: Frame) -> bytes:
    validate(frame)
    content = (
        HEADER.pack(
            MAGIC,
            VERSION,
            frame.kind,
            frame.session,
            frame.sequence,
            frame.total,
            len(frame.payload),
        )
        + frame.payload
    )
    return content + CRC.pack(zlib.crc32(content))


def decode(datagram: bytes) -> Frame:
    if (
        not isinstance(datagram, bytes)
        or not HEADER.size + CRC.size <= len(datagram) <= MAX_DATAGRAM
    ):
        raise ValueError("Datagram size outside bounds")
    magic, version, kind, session, sequence, total, length = HEADER.unpack_from(
        datagram
    )
    if magic != MAGIC or version != VERSION:
        raise ValueError("Unsupported magic/version")
    if len(datagram) != HEADER.size + length + CRC.size:
        raise ValueError("Declared length mismatch")
    if CRC.unpack_from(datagram, len(datagram) - CRC.size)[0] != zlib.crc32(
        datagram[: -CRC.size]
    ):
        raise ValueError("Checksum mismatch")
    try:
        frame = Frame(
            Kind(kind), session, sequence, total, datagram[HEADER.size : -CRC.size]
        )
    except ValueError as error:
        raise ValueError("Unknown frame kind") from error
    validate(frame)
    return frame


class Sender:
    """One outstanding frame. `poll` and `accept` use a caller-supplied clock."""

    def __init__(
        self,
        payload: bytes,
        session: bytes,
        *,
        timeout: float = 0.045,
        max_attempts: int = 4
    ):
        if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
            raise ValueError("Payload must be bytes within transfer limit")
        if not isinstance(session, bytes) or len(session) != 16:
            raise ValueError("Session must be 16 bytes")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Positive finite timeout required")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 16:
            raise ValueError("Attempt budget must be 1..16")
        self.chunks = [
            payload[i : i + MAX_CHUNK] for i in range(0, len(payload), MAX_CHUNK)
        ]
        self.digest = hashlib.sha256(payload).digest()
        self.session = session
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.sequence = 0
        self.attempts = 0
        self.deadline = None
        self.status = "ACTIVE"
        self.failure = ""
        self.sent = 0
        self.retransmissions = 0

    def poll(self, now: float) -> Frame | None:
        if not math.isfinite(now):
            raise ValueError("Clock must be finite")
        if self.status != "ACTIVE" or (
            self.deadline is not None and now < self.deadline
        ):
            return None
        if self.attempts >= self.max_attempts:
            self.status = "FAILED"
            self.failure = (
                "DONE_TIMEOUT" if self.sequence == len(self.chunks) else "ACK_TIMEOUT"
            )
            return None
        total = len(self.chunks)
        kind = Kind.FIN if self.sequence == total else Kind.DATA
        payload = self.digest if kind == Kind.FIN else self.chunks[self.sequence]
        if self.attempts:
            self.retransmissions += 1
        self.attempts += 1
        self.sent += 1
        self.deadline = now + self.timeout
        return Frame(kind, self.session, self.sequence, total, payload)

    def accept(self, frame: Frame) -> str:
        validate(frame)
        if self.status != "ACTIVE":
            return "sender_terminal"
        if frame.session != self.session:
            return "wrong_session"
        if frame.total != len(self.chunks):
            return "wrong_total"
        if not self.attempts:
            return "unsolicited_reply"
        if frame.sequence != self.sequence:
            return "stale_or_future_reply"
        if self.sequence == len(self.chunks):
            if frame.kind != Kind.DONE or frame.payload != self.digest:
                return "invalid_completion_reply"
            self.status = "COMPLETE"
            return "accepted_done"
        if frame.kind != Kind.ACK:
            return "unexpected_reply_kind"
        self.sequence += 1
        self.attempts = 0
        self.deadline = None
        return "accepted_ack"


class Receiver:
    """One configured session; repeated DATA/FIN replies survive lost replies."""

    def __init__(self, session: bytes):
        if not isinstance(session, bytes) or len(session) != 16:
            raise ValueError("Session must be 16 bytes")
        self.session = session
        self.total = None
        self.chunks = []
        self.complete = False
        self.duplicates = 0
        self.digest = None

    @property
    def payload(self) -> bytes:
        return b"".join(self.chunks)

    def accept(self, frame: Frame) -> tuple[Frame | None, str]:
        validate(frame)
        if frame.session != self.session:
            return None, "wrong_session"
        if frame.kind not in (Kind.DATA, Kind.FIN):
            return None, "unexpected_request_kind"
        if self.total is not None and frame.total != self.total:
            return None, "wrong_total"
        if frame.kind == Kind.DATA:
            if frame.sequence > len(self.chunks):
                return None, "future_data"
            if frame.sequence < len(self.chunks):
                if self.chunks[frame.sequence] != frame.payload:
                    return None, "conflicting_duplicate"
                self.duplicates += 1
                return (
                    Frame(Kind.ACK, self.session, frame.sequence, frame.total),
                    "duplicate_data",
                )
            if self.complete:
                return None, "data_after_completion"
            self.total = frame.total
            self.chunks.append(frame.payload)
            return (
                Frame(Kind.ACK, self.session, frame.sequence, frame.total),
                "accepted_data",
            )
        if len(self.chunks) != frame.total:
            return None, "incomplete_transfer"
        digest = hashlib.sha256(self.payload).digest()
        if frame.payload != digest:
            return None, "digest_mismatch"
        repeated = self.complete
        self.total = frame.total
        self.digest = digest
        self.complete = True
        if repeated:
            self.duplicates += 1
        return Frame(Kind.DONE, self.session, frame.sequence, frame.total, digest), (
            "duplicate_fin" if repeated else "accepted_fin"
        )
