# Apple M3 Ultra: initial Metal prover validation, 2026-11-24

The Metal arithmetic and service tests passed on a real Apple GPU. All 60 primary benchmark proofs verified with the separate stock CPU executable. The broader CPU service suite exposed six failures in macOS monitoring and executable fingerprinting; these are recorded below. This is an initial desktop measurement, not a controlled performance certification.

## Revision and machine

- Prover source: [`6bcc6a56e85e584357a67b3bd8a15fb8fda4e2e8`](https://github.com/midnightntwrk/midnight-zk/pull/667/commits/6bcc6a56e85e584357a67b3bd8a15fb8fda4e2e8). No production code was changed for these runs.
- Mac Studio, Apple M3 Ultra, 32 CPU cores (24 performance, 8 efficiency), 80 GPU cores, 256 GiB unified memory.
- macOS 27.2.1, Rust 1.90.0. Release builds, locked dependencies. Tests used 16 Rayon threads unless the fixture selected its own pool; benchmarks used 24 for both builds.
- No offline Metal compiler was available. The backend embedded its shader source and successfully compiled it through Metal at runtime. This is GPU execution, not a CPU stub.
- Test parameters and synthetic inputs only. The deterministic test SRS is unsuitable for real proofs. No SRS, proving-key cache or private user data is included in this result directory.
- Binary hashes, run timestamps and settings: [environment.json](environment.json). Per-process load samples and aggregate CPU-use observations: [events.jsonl](events.jsonl).

## Tests

| Test group | Passed | Failed | Ignored |
|---|---:|---:|---:|
| Metal library, MSM, transforms and quotient | 56 | 0 | 1 stress test + 2 documentation examples |
| Metal prover service, including seeded byte equality and checked erasure | 5 | 0 | 0 |
| CPU service unit tests | 38 | 2 | 0 |
| CPU service integration tests | 47 | 4 | 0 |
| Total | 146 | 6 | 3 |

`cargo clippy` on the Metal and service crates, all targets, with warnings denied, also passed. The expected shader-source fallback diagnostic is a build-script warning. Summaries: [test-summary.json](test-summary.json). Full logs: [tests/](tests/).

The Metal tests compare arithmetic with CPU references, check unchanged inputs on decline, check seeded CPU/GPU proof byte equality, verify actual device use, exercise resident keys and release, and detect a planted nonzero byte in an erasure check. The default ignored transform stress test uses 2^26 elements and was not run.

### macOS failures reproduced

| Failing test | Observation |
|---|---|
| `report::tests::host_memory_is_read` | Host high-water mark is zero. |
| `service::tests::bound_is_checked_when_verification_fails` | Host-memory limit does not cause the expected retirement after a verification failure. |
| `qa3::memory_bound_overrun_replies_then_retires_restartable` | Host-memory limit does not cause the expected retirement after a reply. |
| `service::cpu_proof_verifies_and_is_labelled_cpu` | Its combined metrics assertion fails because host high-water mark is zero. |
| `oracle_process::start_record_contains_executable_sha256` | Start record lacks the executable digest. |
| `qa3::start_record_has_the_binary_sha256` | Start record lacks the executable digest. |

Source inspection identifies two platform assumptions in `gpu-prover/src/report.rs`: `host_hwm_bytes()` reads `/proc/self/status`, and `binary_sha256()` opens `/proc/self/exe`. Those paths do not exist on macOS. The PR already states that the host-memory bound acts only on Linux; the general service tests nevertheless assert these behaviors on macOS. These failures are outside the passing Metal arithmetic suite. No production fix or test suppression was applied.

The combined CPU command stopped after its unit-test failures, so integration targets were run separately. A later integration target failure also stopped that invocation; `qa3` and `service` were then run explicitly with `--no-fail-fast`. The table counts each test once. CUDA and Lean/Quint conformance were not run in this Mac session.

## Initial CPU versus Metal timings

Warm median of five proofs from one process per backend and size. Each process also made one cold proof. CPU ran first in this initial paired set. All 60 proofs verified; 30 used the Metal build. Device error counts were zero. Every Metal row records completed GPU MSM and quotient work.

| k | CPU warm median (s) | Metal warm median (s) | CPU / Metal | Metal tracked peak (MiB) |
|---|---:|---:|---:|---:|
| 14 | 0.076619 | 0.056860 | 1.35x | 39.0 |
| 16 | 0.223129 | 0.102514 | 2.18x | 140.8 |
| 18 | 0.792533 | 0.232903 | 3.40x | 457.1 |
| 20 | 3.124385 | 0.760763 | 4.11x | 1680.3 |
| 21 | 6.231895 | 1.406318 | 4.43x | 3266.4 |

Raw rows: [all-rows.tsv](all-rows.tsv), with the original per-process rows in [raw/](raw/). Machine-readable summary, including warm ranges and cold medians: [summary.json](summary.json).

The stock CPU build has no GPU feature. The Metal build also enables extra CPU parallelism via `gpu -> prover-par`, and by default shares some MSM work with the CPU. These ratios therefore describe the complete configurations; they do not isolate GPU hardware alone.

Default size thresholds were preserved. At small sizes the backend intentionally declines some operations with `below-min-k`; k=14 reports zero base-domain NTT calls while MSM and quotient work run on the GPU. The benchmark's `fallbacks` column sums all hook declines, including these expected threshold choices. Every recorded backend decline in this session was `below-min-k`; there were no unexpected device-error, memory or shape declines.

The timing covers transcript initialization, witness synthesis, proof creation and transcript finalization. It excludes setup, checked key loading, device registration and shader initialization, cleanup and verification. The `cold` row is the first proof in a prepared process, not complete application startup. Tracked device peak is not total system or driver memory.

The machine remained an active desktop. The initial runner used a one-minute start-load limit of 16 on 32 CPU cores, rather than the upstream benchmark's default of 2; actual loads are preserved in every row. There was no thermal, energy or continuous utilization capture. Five warm samples and one process per configuration are useful initial evidence but insufficient for a firm hardware ranking or regression claim.

## CPU parallelism control

Metal also enables `midnight-proofs/prover-par`. An additional CPU-only k=21 process, built with `prover-par`, used the same cache, 24 threads and one cold plus five warm proofs. All six proofs verified. Its warm median was **5.377025 s**, versus **1.406318 s** on Metal: **3.82x**. Stock CPU / parallel CPU was **1.16x**. This control was collected after the primary session, so it is preliminary and is not an interleaved A/B study.

Raw control: [cpu-par-k21.tsv](cpu-par-k21.tsv). Binary SHA-256: `261be8b26c59de65e574afb5b394d290999e53737a126aa73690bd11d573a144`.

## Reproduction

Run from the repository root on Apple silicon with Rust 1.90.0 and Python 3.11. Keep result output separate from this committed evidence.

```sh
cargo +1.90.0 fetch --locked
export CARGO_BUILD_JOBS=8 RAYON_NUM_THREADS=16 CARGO_PROFILE_DEV_DEBUG=0
cargo +1.90.0 test --offline --locked --release -p midnight-metal -- --test-threads=1
MIDNIGHT_METAL_REQUIRE_DEVICE=1 cargo +1.90.0 test --offline --locked --release -p midnight-gpu-prover --features metal,test-seed --test metal -- --test-threads=1
cargo +1.90.0 clippy --offline --locked -p midnight-metal -p midnight-gpu-prover --all-targets --features midnight-gpu-prover/metal -- -D warnings
# This command is expected to fail on the six platform assertions above.
cargo +1.90.0 test --offline --locked --release -p midnight-gpu-prover --no-default-features --no-fail-fast -- --test-threads=1

export RUSTUP_TOOLCHAIN=1.90.0 CARGO_TARGET_DIR="$PWD/target"
export RC1_WORK_DIR="$PWD/target/mac-validation/bench" RAYON_NUM_THREADS=24
bash bench/gpu-prover/rc1/scripts/build-metal.sh
python3 bench/gpu-prover/rc1/results/apple-m3-ultra-2026-11-24/run-session.py \
  --out target/mac-validation/session --bin-dir "$RC1_WORK_DIR/bin" \
  --threads 24 --warm 5 --repeats 1 --max-load 16

# CPU-only control with the parallelism also enabled by the GPU feature.
cargo +1.90.0 build --offline --locked --release -p midnight-gpu-prover-bench --bin rc1-bench --features midnight-proofs/prover-par
install -m755 target/release/rc1-bench "$RC1_WORK_DIR/bin/rc1-cpu-par"
RC1_K=21 RC1_REP=1 RC1_WARM=5 RC1_MAX_LOAD=16 \
  RC1_CACHE="$PWD/target/mac-validation/session/test-key-cache" \
  "$RC1_WORK_DIR/bin/rc1-cpu-par" run > target/mac-validation/cpu-par-k21.tsv
```

The captured runner refuses incomplete sequences, failed verification, missing GPU MSM/quotient work, device errors and unexpected declines. It records expected threshold declines. For a longer study, use three paired repeats on a quiet machine and a stricter load limit. The original test logs used `--nocapture` for the Metal tests; intentional panic-handling tests therefore include panic text despite passing. Saved logs have workspace paths redacted and trailing whitespace normalized.
