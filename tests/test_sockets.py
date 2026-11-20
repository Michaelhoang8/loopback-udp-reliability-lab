import random
import threading
import unittest

from udp_lab.transport import SCENARIOS, run_transfer

PAYLOAD = random.Random(2026).randbytes(2701)


class LoopbackIntegrationTests(unittest.TestCase):
    def test_repeated_actual_socket_fault_matrix(self):
        for repeat in range(2):
            for scenario in SCENARIOS:
                with self.subTest(repeat=repeat, scenario=scenario):
                    result, events = run_transfer(PAYLOAD, scenario=scenario)
                    if scenario == "drop_all_done":
                        self.assertEqual(result["sender_status"], "FAILED")
                        self.assertEqual(result["failure"], "DONE_TIMEOUT")
                    else:
                        self.assertEqual(result["sender_status"], "COMPLETE")
                    self.assertTrue(result["receiver_complete"])
                    self.assertTrue(result["bytes_match"])
                    self.assertEqual(result["source_sha256"], result["received_sha256"])
                    self.assertTrue(
                        all(
                            address[0] == "127.0.0.1"
                            for address in result["socket_addresses"].values()
                        )
                    )
                    self.assertLess(result["sender_duration_ms"], 1500)
                    self.assertTrue(events)
        self.assertFalse(
            any(t.name in ["udp-receiver", "udp-relay"] for t in threading.enumerate())
        )

    def test_single_attempt_loss_does_not_claim_completion(self):
        for scenario in ["drop_data", "drop_ack", "drop_final_done"]:
            with self.subTest(scenario=scenario):
                result, _ = run_transfer(PAYLOAD, scenario=scenario, max_attempts=1)
                self.assertEqual(result["sender_status"], "FAILED")
                self.assertEqual(result["retransmissions"], 0)
                self.assertEqual(
                    result["receiver_complete"], scenario == "drop_final_done"
                )

    def test_empty_actual_transfer(self):
        result, _ = run_transfer(b"")
        self.assertEqual(result["sender_status"], "COMPLETE")
        self.assertTrue(result["receiver_complete"])
        self.assertEqual(result["received_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
