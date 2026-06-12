# load_tmvs rework, round 1: C-level findings (2026-06-12, Apple M4 Pro)

Continues ROADMAP item 1 from a new environment: M4 Pro (macOS arm64).
No valgrind here, so methodology is in-decoder nanosecond timers around
the load_tmvs DSP call (temporary patch, not committed) plus interleaved
same-session A/B; the GitHub Actions CI provides cachegrind counters on
x86-64 and arm64 Linux for anything landed.

## Whole-decoder share on M4 (NEON asm, threads=1)

- screen-content streams: load_tmvs = **14.5–15%** of decode wall time
  (121 ms of 820 ms over 1500 frames of 1080p screen), matching the
  16.7% (of instructions, SSE4) seen on x86.
- Encoder caveat that cost an hour: SVT-AV1 v3.1.2 (brew) sets
  `use_ref_frame_mvs` only on screen-class clips of our corpus — the
  smooth/detail/motion/grain clips never call load_tmvs when re-encoded
  with it. The original container corpus (apt SVT-AV1) exercised it on
  all inter clips. aomenc enables ref-frame-mvs by default and was used
  to produce a dense-motion stressor (life source, 720p, cq 40).

## Kernel C vs NEON on M4 (checkasm --bench, 5 seeds)

NEON over C: only **1.05–1.17x** — the arm mirror of the x86 finding
(SSE4 1.37x, seed-bimodal). On two ISAs, hand-written SIMD barely beats
the C: the kernel is algorithm-bound, not instruction-selection-bound.

## Phase attribution (C path, 1500-frame screen stream)

Temporary counters inside load_tmvs_c:

- INVALID-fill: 13.1 ms (10%); projection scan: 112.8 ms (90%).
- 28.6M outer iterations: **87.5% are single-cell ref==0 skips**,
  5.3% ref2ref==0 skips; only 2.07M start a projecting run.
- Runs average **52 identical cells**; 108.6M cells scanned in the
  run-continuation loop, 107.8M of them written (the per-cell window
  test passes 99.3% of the time).

## Attempt 1: run/span restructure — REJECTED by measurement

Restructured load_tmvs_c to (a) batch-skip unusable cells via a
per-mfmv-ref `usable[8]` table, (b) find each maximal identical-(ref,mv)
run first, (c) project once per run and write the target span with the
per-8-col-group window test reduced to an interval intersection.
Bit-exact by construction (same write order/values); verified by
checkasm refmvs over 10 seeds against the NEON asm plus the full gate.

In-decoder forced-C interleaved A/B (medians, threads=1):

| stream | old C | new C | delta |
|---|---|---|---|
| screen 1080p 1500f (runs ~52) | 129.5 ms | 114.0 ms | **−12%** (beats NEON's 121.3) |
| aomenc motion 720p (runs ~1–2) | 49.6 ms | 57.9 ms | **+17%** |

Why the dense regression: the old per-cell loop runs at ~3 cycles/cell
with IPC ≈ 4.3 — it is latency-bound on the per-cell load→compare→branch
chain, which an out-of-order core already overlaps almost perfectly.
Removing instructions around that chain buys little (hence −12%, not
−2x, on long runs), and per-run setup (projection + interval math)
cannot amortize over 1–2-cell runs (hence +17% dense). A scalar
restructure cannot beat the OoO engine at its own game. Reverted;
recorded here as the design basis for the SIMD round.

## Attempt 2: usable[] skip-LUT alone — LANDED

Keeping the original per-cell structure and only merging the two skip
tests (ref==0, ref2ref==0) into one table-driven scan loop:

| stream | delta (forced C, interleaved A/B, 5 rounds) |
|---|---|
| screen 1080p | **−4.0%** (135.0 → 129.7 ms) |
| aomenc dense motion | **−1.6%** (48.6 → 47.8 ms) |

Both regimes improve; full gate + 10-seed checkasm pass; dense aomenc
stream decodes bit-exact vs a clean master build on SIMD and pure-C
paths. Affects the C path only — value today on no-asm platforms
(RISC-V has no refmvs asm) and as the reference for the asm rework.

## Design implications for the SIMD round (the actual prize)

The serial chain must become per-16-cell vector work; everything below
falls out of the measured workload shape:

1. **Vectorized skip-scan**: 16 cells = 80 bytes = 5 vector regs; TBL
   (NEON) / pshufb (x86) gathers the 16 ref bytes (stride 5), a second
   TBL through `usable[]` turns them into a skip mask; find-first-set
   locates the next candidate. ~0.2 cycles/cell vs ~3 today across the
   87.5% skip majority.
2. **Vectorized run-find**: same gather yields ref-byte and mv-word
   vectors; CMEQ against splats of the run head finds the run end
   without a per-cell branch. Crucially this costs the same for short
   runs, eliminating the dense-content regression mode of attempt 1.
3. **Span writes as pattern stores**: within a run the output is a
   repeating 5-byte (mv,ref) pattern; LCM(5,16) = 80, so 5 precomputed
   vectors tile the span exactly. Window clipping is the interval
   intersection from attempt 1.
4. Expected kernel headroom from the cycle accounting: ~3–4x on
   skip/run-heavy content (→ ~10% whole-decoder on screen streams),
   ~1.3–1.5x on dense content (per-run scalar projection irreducible).

## Tooling notes

- The checkasm bench fixture for load_tmvs is adversarial to exactly
  this rework: it sets ref2ref=1 for all refs (skip-scan never skips)
  over random short runs. Kernel benches of the SIMD round must be
  read with that in mind (and run over ≥3 seeds per the standing rule);
  the in-decoder A/B on both content classes is the deciding metric.
- GitHub arm64 runners (Neoverse): apt valgrind 3.22 cannot decode
  SVE/I8MM instructions, killing 6 of 7 1080p clips under cachegrind;
  the workflow now masks counter runs to the dotprod tier (cpumask 3).
  Gate steps still run the full instruction set.
