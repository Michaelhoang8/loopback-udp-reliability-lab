"""Reconcile traces, captured payload bytes, accounting and source provenance."""

import argparse
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]


def verify(output: Path):
    archived_manifest = output / "results/measurement-sources/manifest.json"
    if archived_manifest.exists():
        archived = json.loads(archived_manifest.read_text(encoding="utf-8"))
        original = (output / archived["archived_path"]).read_bytes()
        original = original.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        assert hashlib.sha256(original).hexdigest() == archived["sha256_lf"]
    environment = json.loads((output / "results/environment.json").read_text())
    payload = random.Random(environment["payload_seed"]).randbytes(
        environment["payload_bytes"]
    )
    expected_hash = hashlib.sha256(payload).hexdigest()
    records = json.loads((output / "results/runs.json").read_text())
    assert len(records) == 16 * environment["repetitions"]
    assert len({r["run_id"] for r in records}) == len(records)
    for record in records:
        summary = json.loads(
            (output / "results/raw" / f'{record["run_id"]}-summary.json').read_text()
        )
        assert record == summary
        received = bytes.fromhex(record["received_payload_hex"])
        assert len(received) == record["received_bytes"]
        assert hashlib.sha256(received).hexdigest() == record["received_sha256"]
        assert expected_hash == record["source_sha256"]
        assert record["bytes_match"] == (received == payload)
        if record["receiver_complete"]:
            assert received == payload
        if record["sender_status"] == "COMPLETE":
            assert record["receiver_complete"] and record["bytes_match"]
        assert all(
            address[0] == "127.0.0.1" and 0 < address[1] < 65536
            for address in record["socket_addresses"].values()
        )
        events = [
            json.loads(line)
            for line in (output / "results/raw" / f'{record["run_id"]}-trace.jsonl')
            .read_text()
            .splitlines()
        ]
        assert [e["event_id"] for e in events] == list(range(len(events)))
        assert [e["time_ns"] for e in events] == sorted(e["time_ns"] for e in events)
        sends = [e for e in events if e["role"] == "sender" and e["event"] == "sent"]
        assert len(sends) == record["sent_frames"]
        assert sum(e["attempt"] > 1 for e in sends) == record["retransmissions"]
        assert all(1 <= e["attempt"] <= record["max_attempts"] for e in sends)
        assert (
            sum(e["event"] in ["duplicate_data", "duplicate_fin"] for e in events)
            == record["receiver_duplicates"]
        )
        assert sum(e["event"] == "accepted_fin" for e in events) == int(
            record["receiver_complete"]
        )
        for action, field in [
            ("drop", "relay_drops"),
            ("duplicate", "relay_duplicates"),
            ("delay", "relay_delays"),
            ("corrupt", "relay_corruptions"),
        ]:
            applied = [
                e for e in events if e["role"] == "relay" and e["event"] == action
            ]
            assert len(applied) == record[field]
            for event in applied:
                assert any(
                    f["action"] == action
                    and f["direction"] == event["direction"]
                    and f["kind"] == event["kind"]
                    and f["sequence"] == event["sequence"]
                    and f["occurrence"] in [None, event["occurrence"]]
                    for f in record["scheduled_faults"]
                )
        assert (
            abs(
                record["sender_duration_ms"]
                - (record["finish_ns"] - record["start_ns"]) / 1e6
            )
            < 1e-9
        )
    manifest = json.loads((output / "results/source-sha256.json").read_text())
    for name, expected in manifest.items():
        assert (
            hashlib.sha256(
                (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            == expected
        ), name
    print(
        f"Verified {len(records)} traces, payload captures, fault accounting and source hashes."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    verify(parser.parse_args().output_dir.resolve())
