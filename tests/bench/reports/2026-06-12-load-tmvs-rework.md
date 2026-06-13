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

## Round 2: NEON implementation (landed, same day)

Both phases landed on this branch; in-decoder interleaved A/B vs the
2021 scalar-transcription NEON asm, M4 Pro, threads=1, medians of 7:

| phase | screen 1080p | dense aomenc 720p |
|---|---|---|
| A: vector skip-scan only | −7.3% | −2.6% |
| B/C: + run-find / span stores (final) | **−55%** | **+7.5%** |

The B/C iteration history is the session's main lesson: the first
draft hit −54% screen but **+36%** dense, and four rounds of removing
data-dependent branches from the run head (+36 → +21 → +19 → +10 →
+7.5%) recovered it while the instruction count barely moved. On a
wide out-of-order core this loop pays for unpredictable branches and
NEON→GPR transfer latency (fmov d→x in the compare-mask path), not for
ALU ops: short-run compares became 64-bit GPR xors (one branch per
run for the 65% single-cell majority, measured by a run-length census:
2.14M of 3.30M runs on the dense clip are length 1, 95k are 8+),
escalating to 16-byte cmeq only past 4 cells.

Whole-decoder effect on M4: ~−8% wall on screen-class streams
(load_tmvs was 15% there), +0.1% on dense motion. The +7.5% dense
kernel regression is the cost of the run abstraction on content with
no runs; an adaptive per-call path selection (both paths are
bit-exact, so switchable freely) is the obvious polish if upstream
wants the regression at exactly zero. Fixture parity: 1.05–1.19x over
C across 5 seeds, same band as the replaced asm.

Port guidance for x86 (the upstream-relevant target, SSE4 2018-era):
the same structure maps directly — pmovmskb replaces the shrn/fmov
mask trick (cheaper there), 64-bit GPR xors work identically, pshufb
replaces tbl for both the stride-5 ref gather and the 5-byte pattern
phases. The dense-content branch discipline is the part to carry
over, not the instruction selection.

## Industry-clip validation (pts/dav1d inputs, 2026-06-12 evening)

`tests/bench/fetch_realworld.sh` now fetches the four streams the
Phoronix `pts/dav1d` profile decodes (Netflix Chimera 8/10-bit 1080p,
Summer Nature 1080p/4K), remuxed to IVF exactly as that profile does.
They live in `data/realworld/`, outside the routine gate. Validation
of the full branch (58 commits) against a clean master build on the
M4 Pro:

- **Bit-exact on all four clips**: full-length SIMD decode at 1 and 4
  threads, plus 2000-frame pure-C spot checks — 11/11 MATCH.
- **Wall-clock neutral within noise** (interleaved A/B, pts arguments
  `--muxer null --filmgrain 0`; medians, branch vs master): at 14
  threads chimera_8b −1.7%, chimera_10b +1.3%, summer_1080p 0%,
  summer_4k 0%; at 1 thread all within ±2% except one noisy
  summer_1080p reading (+3.7%). As expected: the series' wins are
  1–3% instruction-level and content-class-specific, below single-run
  wall noise on film content.
- **load_tmvs on real film content sits in the dense regime**:
  Chimera 8-bit calls it 19.3k times per 2000 frames at ~2.4% of
  single-thread decode; the reworked NEON kernel measured ~+3.8%
  there (356 vs 343 ms, single pair) — i.e. ~+0.09% whole-decoder,
  invisible in wall-clock, consistent with the +7.5% bound from the
  aomenc stressor. The screen-content win does not generalize to film
  content, and the regression does not hurt it measurably either.
- arm64 CI counters (same evening, after the VEX workaround): the
  full 7-clip A/B now runs; branch vs master instructions/frame
  −1.69…−15.38% (screen −15.38% is the NEON rework under cachegrind
  on Neoverse). One investigated flag, now resolved: smooth-10bit D1
  read misses +22% per frame (604k → 738k) with last-level misses
  flat (L1 pressure only, no extra DRAM traffic). A phase-A-only
  bisect run (skip-LUT + vectorized skip-scan, no run/span rework)
  measured smooth-10bit D1 at **−7.24%** — i.e. phase A *reduces* it,
  so the regression is entirely the phase B/C run-find: its two
  overlapping 16-byte read-ahead loads (`[p-4]`/`[p+1]`, then the
  q-loads) touch one extra cache line per run boundary, and on
  smooth-10bit's long runs (avg 52 cells, like screen) that scan
  reads further ahead than the old per-cell loop. Instructions still
  drop (−1.69%), LL is flat, and it is bit-exact — an L1-only
  microarchitectural cost on one intra-heavy content class where
  load_tmvs is ~6% of decode, not a memory-traffic regression. If it
  ever matters, bounding the run-find read-ahead to the run length
  already known from the GPR fast path would remove it.

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
