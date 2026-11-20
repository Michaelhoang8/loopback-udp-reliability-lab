"""Actual sockets bound exclusively to 127.0.0.1, with an instrumented relay."""

from dataclasses import asdict, dataclass
import hashlib
import heapq
import socket
import threading
import time

from .protocol import Frame, Kind, MAX_DATAGRAM, Receiver, Sender, decode, encode

LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class Fault:
    direction: str
    kind: str
    sequence: int
    action: str
    occurrence: int | None = 1
    delay_seconds: float = 0.0


SCENARIOS = {
    "baseline": [],
    "drop_data": [Fault("to_receiver", "DATA", 2, "drop")],
    "drop_ack": [Fault("to_sender", "ACK", 1, "drop")],
    "duplicate_data": [
        Fault("to_receiver", "DATA", 1, "duplicate", delay_seconds=0.008)
    ],
    "delayed_ack": [Fault("to_sender", "ACK", 1, "delay", delay_seconds=0.09)],
    "corrupt_data": [Fault("to_receiver", "DATA", 2, "corrupt")],
    "drop_final_done": [Fault("to_sender", "DONE", 6, "drop")],
    "drop_all_done": [Fault("to_sender", "DONE", 6, "drop", occurrence=None)],
}


class Trace:
    def __init__(self):
        self.origin = time.perf_counter_ns()
        self.events = []
        self.lock = threading.Lock()

    def emit(self, role: str, event: str, frame: Frame | None = None, **extra):
        with self.lock:
            row = {
                "event_id": len(self.events),
                "time_ns": time.perf_counter_ns() - self.origin,
                "role": role,
                "event": event,
            }
            if frame is not None:
                row.update(
                    kind=frame.kind.name,
                    sequence=frame.sequence,
                    total=frame.total,
                    session=frame.session.hex(),
                    payload_bytes=len(frame.payload),
                )
            row.update(extra)
            self.events.append(row)


def _socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LOOPBACK, 0))
    sock.settimeout(0.003)
    return sock


def run_transfer(
    payload: bytes,
    *,
    scenario: str = "baseline",
    max_attempts: int = 4,
    timeout: float = 0.045,
    transfer_limit: float = 1.5
) -> tuple[dict, list[dict]]:
    """Run one finite local transfer. Fault keys are direction/kind/seq/occurrence.

    The stock completion-loss schedules target six-chunk study payloads. Custom
    payloads are allowed, but those schedules only apply if their keys are reached.
    """
    if scenario not in SCENARIOS:
        raise ValueError("Unknown predeclared fault scenario")
    if not 0.1 <= transfer_limit <= 3:
        raise ValueError("Transfer deadline must be 0.1..3 seconds")
    session = hashlib.sha256(
        payload + scenario.encode() + bytes([max_attempts])
    ).digest()[:16]
    sender = Sender(payload, session, timeout=timeout, max_attempts=max_attempts)
    receiver = Receiver(session)
    trace = Trace()
    stop = threading.Event()
    errors = []
    sockets = []
    threads = []
    sender_socket = receiver_socket = relay_socket = None
    start_ns = finish_ns = 0
    try:
        for _ in range(3):
            sockets.append(_socket())
        sender_socket, receiver_socket, relay_socket = sockets
        sender_address = sender_socket.getsockname()
        receiver_address = receiver_socket.getsockname()
        relay_address = relay_socket.getsockname()

        def guarded(function):
            def wrapped():
                try:
                    function()
                except Exception as error:
                    if not stop.is_set():
                        errors.append(repr(error))
                        trace.emit("harness", "thread_failure", error=repr(error))
                        stop.set()

            return wrapped

        def receive_loop():
            while not stop.is_set():
                try:
                    datagram, address = receiver_socket.recvfrom(MAX_DATAGRAM + 1)
                except socket.timeout:
                    continue
                if address != relay_address:
                    trace.emit("receiver", "wrong_peer")
                    continue
                try:
                    frame = decode(datagram)
                except ValueError as error:
                    trace.emit(
                        "receiver",
                        "invalid_datagram",
                        reason=str(error),
                        bytes=len(datagram),
                    )
                    continue
                trace.emit("receiver", "received", frame)
                reply, reason = receiver.accept(frame)
                trace.emit("receiver", reason, frame)
                if reply is not None:
                    receiver_socket.sendto(encode(reply), relay_address)
                    trace.emit("receiver", "sent", reply)

        def relay_loop():
            counts = {}
            pending = []
            serial = 0
            while not stop.is_set():
                now = time.monotonic()
                while pending and pending[0][0] <= now:
                    _, _, data, target, frame, direction = heapq.heappop(pending)
                    relay_socket.sendto(data, target)
                    trace.emit("relay", "forwarded", frame, direction=direction)
                try:
                    datagram, address = relay_socket.recvfrom(MAX_DATAGRAM + 1)
                except socket.timeout:
                    continue
                if address == sender_address:
                    direction, target = "to_receiver", receiver_address
                elif address == receiver_address:
                    direction, target = "to_sender", sender_address
                else:
                    trace.emit("relay", "wrong_peer")
                    continue
                try:
                    frame = decode(datagram)
                except ValueError as error:
                    trace.emit("relay", "invalid_datagram", reason=str(error))
                    continue
                key = (direction, frame.kind.name, frame.sequence)
                occurrence = counts.get(key, 0) + 1
                counts[key] = occurrence
                trace.emit(
                    "relay",
                    "received",
                    frame,
                    direction=direction,
                    occurrence=occurrence,
                )
                matching = [
                    f
                    for f in SCENARIOS[scenario]
                    if (f.direction, f.kind, f.sequence) == key
                    and (f.occurrence is None or f.occurrence == occurrence)
                ]
                fault = matching[0] if matching else None
                delays = [0.0]
                if fault:
                    trace.emit(
                        "relay",
                        fault.action,
                        frame,
                        direction=direction,
                        occurrence=occurrence,
                        delay_seconds=fault.delay_seconds,
                    )
                    if fault.action == "drop":
                        continue
                    if fault.action == "delay":
                        delays = [fault.delay_seconds]
                    elif fault.action == "duplicate":
                        delays = [0.0, fault.delay_seconds]
                    elif fault.action == "corrupt":
                        datagram = datagram[:-1] + bytes([datagram[-1] ^ 1])
                for delay in delays:
                    serial += 1
                    heapq.heappush(
                        pending,
                        (
                            time.monotonic() + delay,
                            serial,
                            datagram,
                            target,
                            frame,
                            direction,
                        ),
                    )

        for name, target in [("udp-receiver", receive_loop), ("udp-relay", relay_loop)]:
            thread = threading.Thread(target=guarded(target), name=name)
            threads.append(thread)
            thread.start()
        start_ns = time.perf_counter_ns() - trace.origin
        hard_deadline = time.monotonic() + transfer_limit
        while sender.status == "ACTIVE" and not stop.is_set():
            now = time.monotonic()
            if now >= hard_deadline:
                sender.status, sender.failure = "FAILED", "HARNESS_DEADLINE"
                break
            outgoing = sender.poll(now)
            if outgoing is not None:
                sender_socket.sendto(encode(outgoing), relay_address)
                trace.emit("sender", "sent", outgoing, attempt=sender.attempts)
            if sender.status != "ACTIVE":
                break
            try:
                datagram, address = sender_socket.recvfrom(MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            if address != relay_address:
                trace.emit("sender", "wrong_peer")
                continue
            try:
                incoming = decode(datagram)
            except ValueError as error:
                trace.emit("sender", "invalid_datagram", reason=str(error))
                continue
            trace.emit("sender", "received", incoming)
            trace.emit("sender", sender.accept(incoming), incoming)
        finish_ns = time.perf_counter_ns() - trace.origin
        trace.emit("sender", "terminal", status=sender.status, failure=sender.failure)
        # Keep receiver/relay alive for the largest scheduled delayed packet. This
        # captures late deliveries after sender completion without extending its latency.
        stop.wait(0.11)
    finally:
        stop.set()
        for sock in sockets:
            sock.close()
        for thread in threads:
            thread.join(timeout=1)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("Loopback thread failed to stop")
    if errors:
        raise RuntimeError("Loopback thread failed: " + "; ".join(errors))
    expected_hash = hashlib.sha256(payload).hexdigest()
    received_hash = hashlib.sha256(receiver.payload).hexdigest()
    events = trace.events
    result = {
        "scenario": scenario,
        "max_attempts": max_attempts,
        "timeout_ms": timeout * 1000,
        "payload_bytes": len(payload),
        "chunks": len(sender.chunks),
        "session": session.hex(),
        "sender_status": sender.status,
        "failure": sender.failure,
        "receiver_complete": receiver.complete,
        "received_bytes": len(receiver.payload),
        "source_sha256": expected_hash,
        "received_sha256": received_hash,
        "received_payload_hex": receiver.payload.hex(),
        "bytes_match": receiver.payload == payload,
        "sent_frames": sender.sent,
        "retransmissions": sender.retransmissions,
        "receiver_duplicates": receiver.duplicates,
        "relay_drops": sum(r["event"] == "drop" for r in events),
        "relay_duplicates": sum(r["event"] == "duplicate" for r in events),
        "relay_delays": sum(r["event"] == "delay" for r in events),
        "relay_corruptions": sum(r["event"] == "corrupt" for r in events),
        "start_ns": start_ns,
        "finish_ns": finish_ns,
        "sender_duration_ms": (finish_ns - start_ns) / 1e6,
        "socket_addresses": {
            "sender": list(sender_address),
            "receiver": list(receiver_address),
            "relay": list(relay_address),
        },
        "scheduled_faults": [asdict(f) for f in SCENARIOS[scenario]],
    }
    return result, events
