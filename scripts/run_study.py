"""Run bounded loopback transfers and render recorded evidence, not simulated timings."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from udp_lab.transport import SCENARIOS, run_transfer

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument(
        "--quick", action="store_true", help="One repetition instead of three"
    )
    parser.add_argument(
        "--reuse", action="store_true", help="Replot only if network sources match"
    )
    args = parser.parse_args()
    out = args.output_dir.resolve()
    raw = out / "results/raw"
    charts = out / "docs/charts"
    for directory in [raw, charts]:
        directory.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "udp_lab").glob("*.py"))
    source_hashes = {
        p.relative_to(ROOT)
        .as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        for p in sources
    }
    if args.reuse:
        manifest = out / "results/source-sha256.json"
        if not manifest.exists():
            parser.error("--reuse needs a previous source manifest")
        recorded = json.loads(manifest.read_text())
        if any(recorded.get(k) != v for k, v in source_hashes.items()):
            parser.error("Network sources changed; run fresh measurements")
        records = json.loads((out / "results/runs.json").read_text())
        environment = json.loads((out / "results/environment.json").read_text())
    else:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(ROOT / "tests"),
                "-v",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        transcript = result.stdout + result.stderr
        print(transcript, end="")
        (out / "results/test-results.txt").write_text(
            transcript, encoding="utf-8", newline="\n"
        )
        result.check_returncode()
        payload = random.Random(2026).randbytes(2701)
        repetitions = 1 if args.quick else 3
        records = []
        for scenario in SCENARIOS:
            for attempts in [1, 4]:
                for repetition in range(repetitions):
                    record, trace = run_transfer(
                        payload, scenario=scenario, max_attempts=attempts
                    )
                    record.update(
                        run_id=f"{scenario}-a{attempts}-r{repetition}",
                        repetition=repetition,
                    )
                    records.append(record)
                    write_json(raw / f'{record["run_id"]}-summary.json', record)
                    (raw / f'{record["run_id"]}-trace.jsonl').write_text(
                        "".join(json.dumps(row) + "\n" for row in trace),
                        encoding="utf-8",
                        newline="\n",
                    )
        environment = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "repetitions": repetitions,
            "payload_seed": 2026,
            "payload_bytes": len(payload),
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
            "clock": "perf_counter_ns for trace; monotonic for retry deadlines",
            "binding": "127.0.0.1 with ephemeral ports",
            "telemetry": "application event instrumentation, not PCAP",
        }
        write_json(out / "results/runs.json", records)
        write_json(out / "results/environment.json", environment)
    source_hashes["scripts/run_study.py"] = hashlib.sha256(
        Path(__file__).read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    write_json(out / "results/source-sha256.json", source_hashes)
    render(out, records, environment)
    print(f"Rendered {len(records)} measured transfers into {out}")


def render(out, records, environment):
    charts = out / "docs/charts"
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "figure.facecolor": "#f1f7fa",
            "axes.facecolor": "#f1f7fa",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titlelocation": "left",
            "axes.titleweight": "bold",
            "savefig.dpi": 150,
        }
    )
    navy, blue, orange = "#142f46", "#3484b0", "#b25f40"
    summaries = []
    for scenario in SCENARIOS:
        for budget in [1, 4]:
            group = [
                r
                for r in records
                if r["scenario"] == scenario and r["max_attempts"] == budget
            ]
            summaries.append(
                {
                    "scenario": scenario,
                    "attempt_budget": budget,
                    "trials": len(group),
                    "sender_completed": sum(
                        r["sender_status"] == "COMPLETE" for r in group
                    ),
                    "receiver_completed": sum(r["receiver_complete"] for r in group),
                    "byte_matches": sum(r["bytes_match"] for r in group),
                    "median_terminal_ms": statistics.median(
                        r["sender_duration_ms"] for r in group
                    ),
                    "median_retransmissions": statistics.median(
                        r["retransmissions"] for r in group
                    ),
                }
            )
    with (out / "results/summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    matrix = np.array(
        [
            [s["sender_completed"] / s["trials"], s["receiver_completed"] / s["trials"]]
            for s in summaries
        ]
    )
    fig, ax = plt.subplots(figsize=(11, 8))
    ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(
        [0, 1], ["Sender received valid DONE", "Receiver verified full transfer"]
    )
    ax.xaxis.tick_top()
    ax.set_yticks(
        range(len(summaries)),
        [
            f'{s["scenario"].replace("_", " ")} / {s["attempt_budget"]} attempt(s)'
            for s in summaries
        ],
    )
    for i, s in enumerate(summaries):
        for j, key in enumerate(["sender_completed", "receiver_completed"]):
            ax.text(
                j,
                i,
                f'{s[key]}/{s["trials"]}',
                ha="center",
                va="center",
                color="white" if matrix[i, j] > 0.5 else navy,
            )
    fig.tight_layout()
    fig.savefig(charts / "completion.png")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 5.7))
    positions = np.arange(len(SCENARIOS))
    for budget, offset, color in [(1, -0.17, navy), (4, 0.17, blue)]:
        selected = [s for s in summaries if s["attempt_budget"] == budget]
        ax.barh(
            positions + offset,
            [s["median_terminal_ms"] for s in selected],
            height=0.3,
            color=color,
            label=f"{budget} attempt(s) per frame",
        )
    ax.set_yticks(positions, [s.replace("_", " ") for s in SCENARIOS])
    ax.invert_yaxis()
    ax.set_xlabel("Median milliseconds until sender success or budget exhaustion")
    ax.legend(frameon=False)
    ax.set_title("Sender terminal time", fontsize=20, pad=15)
    fig.tight_layout()
    fig.savefig(charts / "terminal-time.png")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for budget, color, marker in [(1, orange, "x"), (4, blue, "o")]:
        run = next(
            r
            for r in records
            if r["scenario"] == "drop_ack"
            and r["max_attempts"] == budget
            and r["repetition"] == 0
        )
        trace = [
            json.loads(line)
            for line in (out / "results/raw" / f'{run["run_id"]}-trace.jsonl')
            .read_text()
            .splitlines()
        ]
        sent = [r for r in trace if r["role"] == "sender" and r["event"] == "sent"]
        x = [(r["time_ns"] - run["start_ns"]) / 1e6 for r in sent]
        y = [r["sequence"] for r in sent]
        ax.plot(
            x,
            y,
            marker=marker,
            color=color,
            label=f'{budget} attempt(s): {run["sender_status"].lower()}',
        )
        ax.axvline(run["sender_duration_ms"], color=color, ls=":", alpha=0.6)
    ax.set_yticks(range(7), [f"DATA {i}" for i in range(6)] + ["FIN 6"])
    ax.set_xlabel("Actual elapsed milliseconds from producer start")
    ax.set_title("First ACK for chunk 1 dropped by relay", fontsize=20, pad=15)
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(charts / "retry-timeline.png")
    plt.close(fig)
    run = next(
        r
        for r in records
        if r["scenario"] == "drop_final_done"
        and r["max_attempts"] == 4
        and r["repetition"] == 0
    )
    trace = [
        json.loads(line)
        for line in (out / "results/raw" / f'{run["run_id"]}-trace.jsonl')
        .read_text()
        .splitlines()
    ]
    events = [
        r
        for r in trace
        if r["role"] == "relay"
        and r.get("kind") in ["FIN", "DONE"]
        and r["event"] in ["received", "forwarded", "drop"]
    ]
    fig, ax = plt.subplots(figsize=(11, 6))
    for x, name in enumerate(["Sender", "Relay", "Receiver"]):
        ax.axvline(x, color="#b8cbd6", lw=1)
        ax.text(x, -0.9, name, ha="center", fontweight="bold", color=navy)
    for index, event in enumerate(events):
        ms = (event["time_ns"] - run["start_ns"]) / 1e6
        ax.text(
            -0.5,
            index,
            f'{ms:.2f} ms  #{event["event_id"]}',
            ha="right",
            va="center",
            fontsize=10,
        )
        if event["event"] == "drop":
            ax.scatter([1], [index], marker="x", color=orange, s=100)
            ax.text(
                1.1, index, "DONE 6 dropped", va="center", color=orange, fontsize=10
            )
            continue
        toward_receiver = event["direction"] == "to_receiver"
        if event["event"] == "received":
            start, end = (0, 1) if toward_receiver else (2, 1)
        else:
            start, end = (1, 2) if toward_receiver else (1, 0)
        ax.annotate(
            "",
            xy=(end, index),
            xytext=(start, index),
            arrowprops={"arrowstyle": "->", "color": blue, "lw": 1.6},
        )
        ax.text(
            (start + end) / 2,
            index - 0.17,
            f'{event["kind"]} {event["sequence"]}',
            ha="center",
            fontsize=10,
            color=navy,
        )
    ax.set_xlim(-1.05, 2.25)
    ax.set_ylim(len(events) - 0.3, -1.4)
    ax.axis("off")
    ax.set_title("Final-reply loss and repeated FIN", fontsize=20, pad=18)
    fig.text(
        0.03,
        0.02,
        "Relay-observed receipt/forward actions. Rows are evenly spaced; labels show actual elapsed time.",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(charts / "final-handshake.png")
    plt.close(fig)
    report(out, records, summaries, environment)


def report(out, records, summaries, environment):
    attempts4 = [r for r in records if r["max_attempts"] == 4]
    verified = sum(r["receiver_complete"] and r["bytes_match"] for r in attempts4)
    table = "| Scenario | Budget | Sender complete | Receiver complete | Median terminal ms | Median retries |\n|---|---:|---:|---:|---:|---:|\n"
    for s in summaries:
        table += f'| {s["scenario"]} | {s["attempt_budget"]} | {s["sender_completed"]}/{s["trials"]} | {s["receiver_completed"]}/{s["trials"]} | {s["median_terminal_ms"]:.2f} | {s["median_retransmissions"]:g} |\n'
    findings = f"""# Measured loopback results

{len(records)} actual transfers, eight predeclared fault schedules, two per-frame attempt budgets. Each trial carries 2,701 seeded bytes in six chunks. {verified}/{len(attempts4)} four-attempt trials reached byte-identical, verified receiver completion.

{table}

The budgets use the same stop-and-wait protocol and direction/kind/sequence/occurrence fault rules. One attempt is an intentionally limited retry budget, not a separately optimized best-effort UDP implementation. Early failure means later rule targets may never be reached. Timer overshoot and scheduling can cause additional retries; inspect per-run records.

Loss of every DONE illustrates uncertainty: the receiver can verify the whole transfer while the sender exhausts its completion-reply budget. This is not exactly-once external execution. CRC32 detects accidental corruption and SHA-256 checks reassembled bytes; neither authenticates a peer. Sessions here are deterministic fixture IDs, not secure anti-replay tokens.

Terminal time includes timeout failure as well as success; it is not a successful-completion latency distribution or WAN throughput comparison. The 110 ms observation tail is excluded from sender duration. Trials run sequentially in fixed order with three repetitions by default. They are local finite exercises, without statistical significance claims. Instrumentation is application logging, not a packet capture.
"""
    (out / "results/findings.md").write_text(findings, encoding="utf-8", newline="\n")
    html_rows = "".join(
        f'<tr><td>{s["scenario"]}</td><td>{s["attempt_budget"]}</td><td>{s["sender_completed"]}/{s["trials"]}</td><td>{s["receiver_completed"]}/{s["trials"]}</td><td>{s["median_terminal_ms"]:.2f}</td></tr>'
        for s in summaries
    )
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Loopback UDP reliability</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f1f7fa;color:#142f46;font:16px/1.55 system-ui,sans-serif}}header{{background:#142f46;color:#edf7fc;padding:50px 6vw}}h1{{font-size:56px;letter-spacing:-2px;line-height:1.08;margin:16px 0 25px}}h2{{font-size:31px;letter-spacing:-.7px;margin:10px 0 25px}}.tag{{font:12px ui-monospace,monospace;letter-spacing:1.5px;color:#3484b0}}header .tag{{color:#96c9e3}}.stats{{display:flex;gap:60px;margin:30px 0}}.stats b{{font-size:34px;display:block}}.stats span{{font-size:12px;color:#b8d3e3}}nav{{display:flex;gap:24px;font:13px ui-monospace,monospace}}nav a{{color:#c1e4f5}}a{{color:#287ca6;text-decoration:none}}main{{max-width:1300px;margin:auto;padding:0 42px}}section{{padding:46px 0;border-bottom:1px solid #c3d4de;scroll-margin-top:10px}}img{{width:100%;display:block}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:30px}}.box{{background:#fff;border-left:3px solid #3484b0;padding:22px}}.wire{{display:flex;font:12px ui-monospace,monospace;margin:25px 0}}.wire div{{padding:20px 13px;border:1px solid #a8c5d5;background:#dfedf5;flex:1}}.wire b{{display:block;font-size:15px;margin-bottom:7px}}.note{{font-size:13px;color:#557181}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}}td,th{{border-bottom:1px solid #c3d4de;padding:12px 8px;text-align:right}}td:first-child,th:first-child{{text-align:left}}th{{font-size:11px;text-transform:uppercase;letter-spacing:.5px}}pre{{white-space:pre-wrap;font:13px/1.6 ui-monospace,monospace}}footer{{padding:30px 0;font-size:12px;color:#557181}}@media(max-width:800px){{h1{{font-size:37px}}main{{padding:0 18px}}.grid{{grid-template-columns:1fr}}.stats{{gap:20px}}nav{{flex-wrap:wrap}}.wire{{flex-wrap:wrap}}}}</style>
<header id="overview"><div class="tag">PYTHON / NETWORK PROTOCOLS / ACTUAL UDP SOCKETS</div><h1>Loopback UDP<br>reliability study</h1><p>Stop-and-wait transfer, bounded retry budgets, locally scheduled packet faults and explicit completion evidence.</p><div class="stats"><div><b>{len(records)}</b><span>measured transfers</span></div><div><b>8</b><span>fault schedules</span></div><div><b>127.0.0.1</b><span>exclusive network binding</span></div></div><nav><a href="#protocol">Framing</a><a href="#retransmission">Retransmissions</a><a href="#completion">Completion</a><a href="#results">Measured results</a><a href="#method">Method</a></nav></header><main>
<section id="protocol"><div class="tag">01 / WIRE FORMAT AND STATE</div><h2>One outstanding frame per transfer</h2><div class="wire"><div><b>LUDP / v1</b>magic + version</div><div><b>Kind</b>DATA ACK FIN DONE</div><div><b>Session</b>16-byte fixture ID</div><div><b>Sequence</b>index + total</div><div><b>Payload</b>length + bytes</div><div><b>CRC32</b>header + payload</div></div><div class="grid"><div class="box"><h3>Bounded state</h3><p>Up to 128 chunks of 512 bytes. ACKs must match session, total, active sequence and expected reply kind. Only one frame is outstanding; retries resend that frame.</p></div><div class="box"><h3>Exact reassembly</h3><p>The receiver accepts in-order chunks, checks duplicate content and replies to duplicates without appending twice. FIN carries the SHA-256 digest of the full payload.</p></div></div><p class="note">Checksums and deterministic fixture sessions provide no authentication or protection against an adversarial peer.</p></section>
<section id="retransmission"><div class="tag">02 / RETRY BEHAVIOR</div><h2>Actual sends after a dropped ACK</h2><img src="charts/retry-timeline.png" alt="Actual sender send times after the first ACK for chunk one is dropped"><p class="note">One recorded trial per budget. Dotted lines mark the sender terminal event. A single-attempt sender stops after timeout; a retry can recover when the receiver acknowledges the duplicate chunk.</p></section>
<section id="completion"><div class="tag">03 / FINAL HANDSHAKE</div><h2>Receiver completion and sender confirmation</h2><img src="charts/final-handshake.png" alt="Measured relay sequence showing dropped DONE and a repeated FIN"><div class="grid"><div class="box"><h3>Lost first DONE</h3><p>The receiver retains completed session state and responds to repeated FIN. A later DONE lets the sender confirm completion.</p></div><div class="box"><h3>Every DONE lost</h3><p>The receiver can finish while the sender fails its retry budget. The trace reports both outcomes. There is no claim of exactly-once external effects.</p></div></div><p class="note">Arrows are relay-observed actions, with evenly spaced rows and actual timestamp labels. They do not measure one-way network latency.</p></section>
<section id="results"><div class="tag">04 / OBSERVED OUTCOMES</div><h2>Completion evidence by fault schedule</h2><img src="charts/completion.png" alt="Sender and receiver completion counts by schedule and retry budget"><img src="charts/terminal-time.png" alt="Measured sender terminal time including failures"><table><thead><tr><th>Scenario</th><th>Attempt budget</th><th>Sender complete</th><th>Receiver complete</th><th>Median terminal ms</th></tr></thead><tbody>{html_rows}</tbody></table><p class="note">Budget comparisons use identical predeclared fault keys. A one-attempt transfer is the same protocol with retries disabled, not a different best-effort implementation. Early failure can prevent later targets from being reached.</p></section>
<section id="method"><div class="tag">05 / MEASUREMENT AND REPRODUCTION</div><h2>Local socket evidence and limits</h2><div class="grid"><div class="box"><h3>Measured execution</h3><p>Three ephemeral loopback UDP sockets: sender, receiver and relay. Faults drop, duplicate, corrupt or delay matching datagrams. Delayed ACKs may arrive after later replies.</p><pre>python -m unittest discover -s tests -v
python scripts/run_study.py --quick
python scripts/run_study.py --output-dir build/my-run</pre></div><div class="box"><h3>Evidence boundaries</h3><p>JSONL contains application instrumentation, not PCAP or NIC timestamps. Send records occur after sendto; cross-thread log order is not a causality clock. Fixed-order finite trials on one host do not estimate WAN performance.</p><p>The receiver stays available through the sender retry window and a 110 ms observation tail. Restart recovery and durable sessions are outside this implementation.</p></div></div><p class="note">Python {environment['python']} · {environment['platform']} · recorded {environment['recorded_at_utc']}</p></section><footer>AI-assisted synthetic protocol exercise. All datagrams stay on 127.0.0.1. Actual socket timings and fixture faults are preserved in the raw evidence.</footer></main></html>"""
    (out / "docs/report.html").write_text(page, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
