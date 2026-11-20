# Wire format and state machines

All integers use network byte order. A datagram is exactly one header, the declared payload and four CRC bytes; trailing data, truncation and oversized input are rejected.

| Field | Bytes | Meaning |
|---|---:|---|
| Magic | 4 | ASCII `LUDP` |
| Version | 1 | `1` |
| Kind | 1 | DATA=1, ACK=2, FIN=3, DONE=4 |
| Session | 16 | Configured fixture identifier |
| Sequence | 4 | Zero-based chunk index, or terminal index |
| Total | 4 | Number of chunks, 0..128 |
| Payload length | 2 | Exact bytes following the header |
| Payload | 0..512 | Chunk, empty ACK or 32-byte digest |
| CRC32 | 4 | CRC over all preceding bytes |

The fixed header is 32 bytes. DATA has a nonempty payload up to 512 bytes, total at least one, and sequence strictly below total. ACK has the same sequence/total constraints and no payload. FIN and DONE have sequence equal to total and exactly 32 payload bytes containing the full-transfer SHA-256 digest. Empty transfers use total=0 and go directly to FIN/DONE.

## Sender

The sender chunks an immutable byte string up to 65,536 bytes and keeps one active sequence. `poll(now)` sends a new DATA or FIN, or retransmits the current frame when its deadline is reached. The first send counts toward the per-frame attempt budget. After the final attempt's deadline expires, the sender becomes `FAILED` with `ACK_TIMEOUT` or `DONE_TIMEOUT`. A late reply does not resurrect a terminal sender.

`accept(frame)` advances only when the sender has sent the current frame, and session, total, sequence and expected reply kind match. Before FIN, the expected reply is ACK. During FIN, the expected reply is DONE with the sender's payload digest. Stale, future, unsolicited and mismatched replies cannot move the sequence. A valid ACK resets the current frame's attempt counter and deadline; a valid DONE marks `COMPLETE`.

The protocol state accepts a caller-supplied finite clock; the socket harness uses `time.monotonic`. It assumes a monotonic clock. The deadline is an observation threshold, not a real-time guarantee: OS scheduling may delay the next poll.

## Receiver

The receiver is configured for one session. Its total becomes fixed after the first accepted chunk, or the empty-transfer FIN. Future DATA is ignored. Earlier DATA is acknowledged again only if its payload exactly matches the stored chunk. A conflicting duplicate cannot replace data. In-order accepted chunks are appended once and remain bounded by the codec's total/chunk limits.

FIN is accepted only after all declared chunks are present and their reassembled digest matches. It marks receiver completion and returns DONE. Completed state is retained while the receiver is alive, so repeated identical FIN returns DONE again. It does not append bytes or repeat an external side effect. The receiver also continues acknowledging matching earlier DATA.

## Completion boundary

The harness keeps receiver and relay alive for the sender's full retry window, then a 110 ms observation tail. This supports the experiment's bounded delayed packets. It is not durable duplicate suppression: restarting the receiver discards session state. There is no finite-message guarantee that both endpoints know the other knows completion. When every DONE is dropped, complete receiver state and failed sender confirmation are both valid observed outcomes.

Both socket endpoints accept datagrams only from the relay's exact local address; the relay accepts only the configured sender/receiver addresses. These are harness isolation checks, not cryptographic authentication. CRC32, SHA-256 and deterministic fixture session IDs do not stop an adversarial sender or establish replay protection.
