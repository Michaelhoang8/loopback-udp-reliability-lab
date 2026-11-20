# Fault schedules and boundaries

The relay matches `(direction, kind, sequence, occurrence)`. Occurrence is counted independently for each direction/kind/sequence key. Rules are declared before execution; outcomes and timings are measured. The same rule set is used for both retry budgets. There is at most one matched rule per received datagram in these fixtures.

| Schedule | Local intervention |
|---|---|
| baseline | Forward every datagram. |
| drop_data | Drop the first DATA for chunk 2. |
| drop_ack | Drop the first ACK for chunk 1. |
| duplicate_data | Forward the first DATA for chunk 1 twice, with a requested 8 ms separation. |
| delayed_ack | Hold the first ACK for chunk 1 for 90 ms; later ACKs can overtake it. |
| corrupt_data | Flip a bit in the first DATA chunk 2 checksum. Receiver codec rejects it. |
| drop_final_done | Drop the first DONE with terminal sequence 6. |
| drop_all_done | Drop every DONE with terminal sequence 6. |

The completion schedules target the study's six-chunk payload. The API allows other payload sizes, but those rule keys only fire if reached. The integration suite explicitly checks empty-transfer behavior separately.

The relay uses a bounded heap of delayed transmissions in these finite fixtures. It is not a general unbounded fault configuration service: only the listed schedules are accepted. Delayed emissions use actual monotonic time and can overshoot requested delays due to scheduling. Duplicates, reordered replies and dropped final confirmation are deliberate model inputs; they do not imply loopback networking itself exhibited those faults.

The one-attempt condition is the same acknowledged stop-and-wait protocol with its retry budget restricted. It still waits for ACK/DONE. It is not unacknowledged streaming and is not presented as an optimized competing protocol. Loss that stops a run early can prevent subsequent rule targets from being reached. Every summary includes the declared rules and actual application counts.

## Excluded cases

No external host traffic, network scanning, exploitation, kernel packet manipulation or administrative privileges. No bandwidth contention model, random bit-error channel, MTU discovery, congestion control, sliding window or adaptive timeout. No authentication, encryption, persistent session store, multi-client receiver or restart recovery. No claim of exactly-once effects outside in-memory byte reassembly.

No claim that retries guarantee delivery under arbitrary loss. Four attempts recover only when sufficient valid requests and replies arrive within the configured finite window. Every-final-reply loss intentionally demonstrates an unresolved sender outcome.
