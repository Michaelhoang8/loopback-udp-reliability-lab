# Measured loopback results

48 actual transfers, eight predeclared fault schedules, two per-frame attempt budgets. Each trial carries 2,701 seeded bytes in six chunks. 24/24 four-attempt trials reached byte-identical, verified receiver completion.

| Scenario | Budget | Sender complete | Receiver complete | Median terminal ms | Median retries |
|---|---:|---:|---:|---:|---:|
| baseline | 1 | 3/3 | 3/3 | 1.23 | 0 |
| baseline | 4 | 3/3 | 3/3 | 1.15 | 0 |
| drop_data | 1 | 0/3 | 0/3 | 61.07 | 0 |
| drop_data | 4 | 3/3 | 3/3 | 46.79 | 1 |
| drop_ack | 1 | 0/3 | 0/3 | 61.05 | 0 |
| drop_ack | 4 | 3/3 | 3/3 | 61.50 | 1 |
| duplicate_data | 1 | 3/3 | 3/3 | 1.17 | 0 |
| duplicate_data | 4 | 3/3 | 3/3 | 0.98 | 0 |
| delayed_ack | 1 | 0/3 | 0/3 | 60.60 | 0 |
| delayed_ack | 4 | 3/3 | 3/3 | 46.77 | 1 |
| corrupt_data | 1 | 0/3 | 0/3 | 60.24 | 0 |
| corrupt_data | 4 | 3/3 | 3/3 | 46.93 | 1 |
| drop_final_done | 1 | 0/3 | 3/3 | 60.24 | 0 |
| drop_final_done | 4 | 3/3 | 3/3 | 61.31 | 1 |
| drop_all_done | 1 | 0/3 | 3/3 | 60.92 | 0 |
| drop_all_done | 4 | 0/3 | 3/3 | 203.52 | 3 |


The budgets use the same stop-and-wait protocol and direction/kind/sequence/occurrence fault rules. One attempt is an intentionally limited retry budget, not a separately optimized best-effort UDP implementation. Early failure means later rule targets may never be reached. Timer overshoot and scheduling can cause additional retries; inspect per-run records.

Loss of every DONE illustrates uncertainty: the receiver can verify the whole transfer while the sender exhausts its completion-reply budget. This is not exactly-once external execution. CRC32 detects accidental corruption and SHA-256 checks reassembled bytes; neither authenticates a peer. Sessions here are deterministic fixture IDs, not secure anti-replay tokens.

Terminal time includes timeout failure as well as success; it is not a successful-completion latency distribution or WAN throughput comparison. The 110 ms observation tail is excluded from sender duration. Trials run sequentially in fixed order with three repetitions by default. They are local finite exercises, without statistical significance claims. Instrumentation is application logging, not a packet capture.
