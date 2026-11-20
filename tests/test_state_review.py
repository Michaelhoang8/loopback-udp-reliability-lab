"""Combined fault schedules checked with a virtual clock and real wire codec."""

from collections import Counter
import heapq
import random
import unittest

from udp_lab.protocol import Receiver, Sender, decode, encode


class CombinedFaultReview(unittest.TestCase):
    def test_first_request_and_reply_loss_with_delayed_duplicates(self):
        for seed in range(30):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                data = rng.randbytes([0, 1, 513, 1537][seed % 4])
                session = seed.to_bytes(16, "big")
                sender = Sender(data, session, timeout=4, max_attempts=4)
                receiver = Receiver(session)
                occurrence = Counter()
                pending = []
                serial = 0

                def enqueue(direction, frame, now):
                    nonlocal serial
                    key = direction, frame.kind, frame.sequence
                    occurrence[key] += 1
                    if occurrence[key] == 1:
                        return  # Lose the first request AND first reply at each stage.
                    packet = encode(frame)
                    serial += 1
                    heapq.heappush(
                        pending, (now + rng.randint(0, 1), serial, direction, packet)
                    )
                    serial += 1
                    # A delayed duplicate can arrive after a later stage has begun.
                    heapq.heappush(
                        pending, (now + rng.randint(2, 7), serial, direction, packet)
                    )

                for clock in range(250):
                    outgoing = sender.poll(float(clock))
                    if outgoing is not None:
                        enqueue("request", outgoing, clock)
                    while pending and pending[0][0] <= clock:
                        _, _, direction, packet = heapq.heappop(pending)
                        frame = decode(packet)
                        if direction == "request":
                            response, _ = receiver.accept(frame)
                            if response is not None:
                                enqueue("reply", response, clock)
                        else:
                            sender.accept(frame)
                    if sender.status != "ACTIVE" and not pending:
                        break
                self.assertEqual(sender.status, "COMPLETE")
                self.assertTrue(receiver.complete)
                self.assertEqual(receiver.payload, data)
                self.assertEqual(len(receiver.chunks), (len(data) + 511) // 512)
                self.assertGreater(sender.retransmissions, 0)
                self.assertIsNone(sender.poll(1000.0))


if __name__ == "__main__":
    unittest.main()
