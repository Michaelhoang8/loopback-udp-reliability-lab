import hashlib
import struct
import unittest
import zlib

from udp_lab.protocol import (
    CRC,
    HEADER,
    MAX_BYTES,
    Frame,
    Kind,
    Receiver,
    Sender,
    decode,
    encode,
)

SESSION = b"0123456789abcdef"


class CodecTests(unittest.TestCase):
    def test_roundtrips_and_maximum_transfer(self):
        payload = bytes(range(256)) * 256
        self.assertEqual(len(payload), MAX_BYTES)
        sender = Sender(payload, SESSION)
        receiver = Receiver(SESSION)
        for clock in range(129):
            frame = sender.poll(float(clock))
            self.assertEqual(decode(encode(frame)), frame)
            reply, _ = receiver.accept(decode(encode(frame)))
            sender.accept(decode(encode(reply)))
        self.assertEqual(sender.status, "COMPLETE")
        self.assertEqual(receiver.payload, payload)
        self.assertEqual(len(receiver.chunks), 128)

    def test_malformed_datagrams(self):
        packet = encode(Frame(Kind.DATA, SESSION, 0, 1, b"a"))
        for malformed in [
            b"",
            packet[:-1],
            packet + b"x",
            b"X" + packet[1:],
            packet[:-1] + bytes([packet[-1] ^ 1]),
            b"x" * 1000,
        ]:
            with self.subTest(packet=malformed[:8]):
                with self.assertRaises(ValueError):
                    decode(malformed)
        # Structurally valid checksummed packets still require semantic validation.
        for kind, seq, total, payload in [
            (99, 0, 1, b"a"),
            (1, 0, 129, b"a"),
            (1, 1, 1, b"a"),
            (2, 0, 1, b"bad"),
            (3, 1, 1, b"short"),
        ]:
            content = (
                HEADER.pack(b"LUDP", 1, kind, SESSION, seq, total, len(payload))
                + payload
            )
            with self.assertRaises(ValueError):
                decode(content + CRC.pack(zlib.crc32(content)))

    def test_public_input_bounds(self):
        for frame in [
            Frame(Kind.DATA, SESSION, 0, 1),
            Frame(Kind.DATA, SESSION, 0, 1, b"x" * 513),
            Frame(Kind.DATA, b"short", 0, 1, b"x"),
            Frame(Kind.ACK, SESSION, -1, 1),
            Frame(Kind.DATA, SESSION, True, 1, b"x"),
        ]:
            with self.assertRaises(ValueError):
                encode(frame)
        for settings in [
            dict(timeout=0),
            dict(timeout=float("nan")),
            dict(max_attempts=0),
            dict(max_attempts=17),
        ]:
            with self.assertRaises(ValueError):
                Sender(b"abc", SESSION, **settings)
        with self.assertRaises(ValueError):
            Sender(b"a" * (MAX_BYTES + 1), SESSION)


class StateMachineTests(unittest.TestCase):
    def test_timeout_exact_boundary_and_budget(self):
        sender = Sender(b"abc", SESSION, timeout=1, max_attempts=3)
        self.assertIsNotNone(sender.poll(10))
        self.assertIsNone(sender.poll(10.999))
        self.assertIsNotNone(sender.poll(11))
        self.assertIsNotNone(sender.poll(12))
        self.assertIsNone(sender.poll(13))
        self.assertEqual((sender.status, sender.failure), ("FAILED", "ACK_TIMEOUT"))
        self.assertEqual((sender.sent, sender.retransmissions), (3, 2))
        self.assertIsNone(sender.poll(14))

    def test_ack_validation_and_replay(self):
        sender = Sender(b"x" * 600, SESSION)
        ack = Frame(Kind.ACK, SESSION, 0, 2)
        self.assertEqual(sender.accept(ack), "unsolicited_reply")
        sender.poll(0)
        invalid = [
            Frame(Kind.ACK, b"0" * 16, 0, 2),
            Frame(Kind.ACK, SESSION, 0, 3),
            Frame(Kind.ACK, SESSION, 1, 2),
            Frame(Kind.DATA, SESSION, 0, 2, b"x"),
        ]
        for frame in invalid:
            sender.accept(frame)
            self.assertEqual(sender.sequence, 0)
        self.assertEqual(sender.accept(ack), "accepted_ack")
        sender.poll(1)
        self.assertEqual(sender.accept(ack), "stale_or_future_reply")
        self.assertEqual(sender.sequence, 1)

    def test_receiver_dedup_conflict_and_future(self):
        receiver = Receiver(SESSION)
        self.assertEqual(
            receiver.accept(Frame(Kind.DATA, SESSION, 1, 2, b"b"))[1], "future_data"
        )
        self.assertIsNone(receiver.total)
        first = Frame(Kind.DATA, SESSION, 0, 2, b"a")
        self.assertEqual(receiver.accept(first)[1], "accepted_data")
        self.assertEqual(receiver.accept(first)[1], "duplicate_data")
        self.assertEqual(receiver.payload, b"a")
        self.assertEqual(
            receiver.accept(Frame(Kind.DATA, SESSION, 0, 2, b"z"))[1],
            "conflicting_duplicate",
        )
        self.assertEqual(
            receiver.accept(Frame(Kind.DATA, SESSION, 1, 3, b"b"))[1], "wrong_total"
        )
        self.assertEqual(
            receiver.accept(Frame(Kind.DATA, b"0" * 16, 1, 2, b"b"))[1], "wrong_session"
        )
        self.assertEqual(receiver.payload, b"a")

    def test_fin_integrity_and_lost_done(self):
        sender = Sender(b"abc", SESSION, timeout=1)
        receiver = Receiver(SESSION)
        premature = Frame(Kind.FIN, SESSION, 1, 1, hashlib.sha256(b"abc").digest())
        self.assertEqual(receiver.accept(premature)[1], "incomplete_transfer")
        reply, _ = receiver.accept(sender.poll(0))
        sender.accept(reply)
        fin = sender.poll(0)
        bad_fin = Frame(Kind.FIN, SESSION, 1, 1, bytes(32))
        self.assertEqual(receiver.accept(bad_fin)[1], "digest_mismatch")
        self.assertFalse(receiver.complete)
        done, reason = receiver.accept(fin)
        self.assertEqual(reason, "accepted_fin")
        self.assertTrue(receiver.complete)
        self.assertEqual(
            sender.accept(Frame(Kind.DONE, SESSION, 1, 1, bytes(32))),
            "invalid_completion_reply",
        )
        repeated = sender.poll(1)
        self.assertEqual(repeated, fin)
        final_reply, reason = receiver.accept(repeated)
        self.assertEqual(reason, "duplicate_fin")
        self.assertEqual(final_reply, done)
        self.assertEqual(sender.accept(final_reply), "accepted_done")
        self.assertEqual(receiver.payload, b"abc")

    def test_empty_transfer_and_final_reply_exhaustion(self):
        sender = Sender(b"", SESSION, timeout=1, max_attempts=2)
        receiver = Receiver(SESSION)
        for clock in [0, 1]:
            frame = sender.poll(clock)
            self.assertEqual(frame.kind, Kind.FIN)
            receiver.accept(frame)  # Every DONE is lost.
        sender.poll(2)
        self.assertEqual(sender.failure, "DONE_TIMEOUT")
        self.assertTrue(receiver.complete)
        self.assertEqual(receiver.payload, b"")


if __name__ == "__main__":
    unittest.main()
