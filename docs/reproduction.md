# Measurement and validation

## Actual execution

Each trial creates three IPv4 UDP sockets and binds them to `127.0.0.1` with port zero, allowing the OS to choose ephemeral ports. The caller runs the sender; one helper thread runs the receiver and another the fault relay. All datagrams go through the relay. No process contacts external addresses.

The payload is 2,701 bytes from `random.Random(2026).randbytes`, producing six chunks. The per-frame timer is 45 ms. The two attempt budgets are one and four. The default run uses three repetitions per configuration, in fixed scenario/budget/repetition order; quick mode uses one. Real scheduling, timer granularity and instrumentation overhead affect timings and occasionally can trigger extra retries. This is not a WAN latency, throughput or statistical performance study.

Sender duration starts after helper-thread startup and ends at sender success or failure. A 110 ms post-terminal observation tail keeps the receiver/relay available for scheduled delayed packets and is excluded from that duration. The harness imposes a separate 1.5-second deadline. Socket receives have a short timeout. Cleanup sets a stop event, closes all sockets and joins both threads with bounded waits, including exceptional paths.

## Trace interpretation

Trace timestamps are `perf_counter_ns` offsets from a per-trial origin. The trace lock assigns consecutive event IDs and serializes instrumentation records. `sent`/`forwarded` records occur after successful `sendto`. Another thread can log a receive before the sending thread logs completion of that call. Thus cross-thread event order is not a causal clock, and differences between role timestamps are not one-way wire latency.

The message-sequence figure uses only the relay's single-thread receipt/forward/drop records. Rows have ordinal spacing for legibility; labels retain actual elapsed milliseconds and event IDs. Arrows represent the observed action, not a measured wire transit interval. JSONL is application instrumentation, not a PCAP, NIC capture or kernel event trace.

## Independent checks

Unit tests exercise exact timeout boundaries and exhaustion with injected clock values, all wire kinds and maximum transfer size, malformed lengths/checksums/types, reply session/sequence/total gates, future chunks, conflicting duplicates, empty transfer, digest mismatch and lost final replies.

The independent review test executes 30 seeded virtual-clock schedules combining the first request/reply loss at every stage with delayed duplicate datagrams and several payload sizes. Real socket tests run the entire fault matrix twice, plus empty and restricted-budget cases. They check actual payload hashes, completion semantics and absence of remaining relay/receiver threads.

`scripts/verify_results.py` independently decodes the captured received payload hex, recomputes hashes from bytes, reconstructs the seeded source payload, and reconciles traces with summaries, retry counts, fault rules, receiver completion and loopback addresses. Source provenance normalizes CRLF to LF before SHA-256 hashing so Git checkout line endings do not invalidate it.

## Commands

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/run_study.py --output-dir build/my-run
python scripts/verify_results.py --output-dir build/my-run
```

Fresh runs produce new ephemeral ports and timing data. They reproduce the experiment, not byte-identical observations. `--reuse` regenerates the report only when the network-source hashes match the measured trial. Chart source changes can be replotted without rerunning sockets. CI checks existing evidence, then runs a quick independent capture into an ignored directory.
