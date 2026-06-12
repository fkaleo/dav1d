# dav1d optimisation roadmap — remaining avenues (2026-06-12)

Consolidated from the measurement sessions recorded in
2026-06-11-baseline.md. Each item carries its evidence basis there.

## Assembly work (tooled: checkasm runs in this environment, see README)

Priorities re-ranked 2026-06-12 after reviewing the upstream
performance-issue inventory (#305/#316/#395/#403): #305 SSSE3 was
promoted to first (highest certainty, commodity hardware) and is now
done (see cross-references below); load_tmvs returns to the top.

1. **`load_tmvs` rework** — strongest case available, now with two
   added datapoints: (a) the SSE4-over-C ratio is heavily
   workload-dependent — an 8-seed census measured
   0.84x/0.86x/0.94x (slower than C) on three seeds and
   1.07-1.36x on five — bimodal by workload shape (real decoding
   uses full-tile widths, likely the favorable regime, so the asm
   should not be dropped) — and any rework must be benchmarked
   across many seeds; (b) vectorizing just the INVALID-fill init with
   overlapping 16-byte stores was implemented and measured ~5% slower
   (store-buffer serialization from the 15-byte advance), then
   reverted. The function is up to 16.7% of cheap-content decode and
   a lazy-projection alternative is ruled out by the usage census.
   This needs an algorithm-level rethink of the projection loop, not
   peephole SIMD.
2. **Sparse-eob fast path for AVX-512 identity itx kernels** — gap
   proven (flat ~127 cycles at all eob vs AVX2's 34→152 scaling), but
   low priority: large IDTX blocks are nearly absent in real streams.
3. **AVX-512 film-grain generation** (`gen_grain_*`) — tops the
   coverage gap list at 18k–97k cycles/kernel; bounded value (runs
   per frame, not per pixel) but the largest uncovered family.
4. **Upstream task-list SIMD items, quantified here**:
   `order_palette()` to DSP (~2.6% on screen content); and the two
   high-ceiling restructurings — dequant moved into itx, and
   diagonal-oriented coefficient contexting — which are the only
   known roads into the 40–64% entropy-decode share.

## Structural / threading (needs bare-metal multithread measurement)

5. **Adaptive threading for cheap streams** — 4-thread decode executes
   +12.7% instructions and loses wall-clock below ~3 ms/frame; fully
   attributed (diffuse scheduler/atomics), fix undesigned. Biggest
   energy win available for the low-power target.
6. **lfmask / left-above ctx zeroing in tile context** (upstream wiki
   item) — analysis done (boundary word-split constraints understood);
   deferred because the benefit is multithread distribution, which a
   shared container cannot measure.
7. **4K cache locality** — after the eob-bounded level-clear patch,
   the remaining LL misses are inherent pixel traffic in the asm
   kernels; only tile/sbrow processing granularity could move it.
   Research-grade.

## Other platforms (the harness in tests/bench/ is portable)

8. **AArch64** — the highest-volume software-AV1 population (older
   phones, pre-AV1-hw ARM laptops, SBCs). The I-cache findings here
   predict the PGO/LTO recipe matters *more* on little cores (A53/A55).
   SVE2 is dav1d's open ARM frontier. GitHub Actions arm64 runners can
   run this whole loop without dedicated hardware.
9. **RISC-V RVV** — thinnest asm coverage in-tree (cdef-dir is
   commented out; no loopfilter/looprestoration/filmgrain asm). The
   pure-C profile census (`bench.py counters --cpumask 0`) is a
   ready-made priority list: prep/put_8tap ~38%, cdef_find_dir ~10%.
10. **Cross-CPU validation of the kernel census** — the Ice Lake
    `checkasm --bench` data should be compared on Zen 4 / Sapphire
    Rapids before acting on the AVX-512 regression tail, and on real
    ARM for the energy story.

## Verification / energy ground truth

11. **Package-energy measurement** — RAPL on bare-metal x86 or
    `powermetrics` on Apple Silicon, to convert the instruction-count
    proxy into joules per video-hour: the project's actual end goal,
    impossible in a container.
12. **Real streaming-ladder content** — Chimera / Summer Nature (the
    Phoronix `pts/dav1d` inputs) once off this network policy, to
    complete the real-content corpus.

## Exhausted — do not revisit without new ideas

Micro-inlining (mined to ~0.1% finds; never inline into decode_b —
I-cache), scalar C DSP rewrites (compiler already optimal: see
cdef_find_dir_c rejection), msac asm (serial floor 1.03–1.75x), lazy
load_tmvs (usage census), itx AVX-512 pointer swaps (workload census).

## Cross-references to upstream GitLab performance issues

- **#395 (worker mutex contention)**: upstream reports degradation
  beyond ~16 cores; our measurement extends this to the low end —
  even at 4 threads, sub-3ms/frame streams run +12.7% instructions
  and slower than 1 thread. Ronald's issue comment contains a design
  (temporary-master + parked-thread lock + atomic progress); the
  harness here is the regression test for implementing it.
- **#316 (AVX-512)**: whole-decoder effect of the completed work is
  +0..+2.3% over AVX2 on Ice Lake (this report); the identity itx
  kernels from !1301 lose to AVX2 below eob~16 (missing sparse fast
  path); generate_grain_* is AVX2-only despite the checklist wording
  (verified in filmgrain_avx512.asm — only fgy/fguv apply kernels).
- **#305 (CDEF 8-bit fully-edged, SSSE3 TODO)**: DONE on this branch
  (patch "x86: add 8-bit fully-edged fast path for cdef_filter_8x8
  SSSE3"): 8x8 kernel -6..-9%, whole-decoder -0.94% instructions at
  --cpumask ssse3. Deliberately limited to 8x8/pure-SSSE3/pri+sec by
  measurement (4x4/4x8 lose to buffer-build overhead; SSE4.1's path
  is already faster). Remaining bounded extension: a sec-only (_01)
  8x8 variant reusing the same buffer. This was prioritized over
  load_tmvs after the upstream work-inventory review, and delivered.
- **#403 (SVE2)**: Martin Storsjo's skepticism (128-bit SVE2 rarely
  beats NEON; single-function ports seldom useful) matches the
  AVX-512 and load_tmvs findings here; treat SVE2 as
  measure-first.
- Gap in upstream tracking: no issue covers the memory-traffic class
  (cf. patches 8/10 here: L1d -12..-37%, LL -41..-82%) or build
  configuration (PGO/LTO: a further -1.8..-3.7% instr, -16% size).

## Environment requirements for the remaining avenues

1. **Any session/machine with repo write access** (a fresh Claude Code
   web session now qualifies) — lands the branch, activates the CI
   workflow, enables upstream MRs and posting findings to
   #305/#316/#395, and reaches code.videolan.org / real Chimera
   content blocked here. Unlocks everything below indirectly. Cost: 0.
2. **GitHub Actions arm64 runners** (free; workflow already in
   .github/) — first non-x86 gate + counters for all 13 patches and
   the PGO/LTO recipe (the I-cache findings predict bigger effects on
   ARM). Paid large runners (up to 64 cores) additionally reproduce
   the #395 mutex-contention regime upstream reports beyond ~16 cores.
3. **Many-core machine (>=16-64 logical cores)** — cloud spot instance
   suffices — the #395 scheduler redesign (Ronald's design in the
   issue) plus our low-core overhead finding: the single biggest
   unimplemented prize. Validation needs scaling curves, impossible
   on 4 shared cores.
4. **Quiet bare-metal x86-64 with root** (any desktop) — wall-clock
   authority for the 1-3.5% instruction wins, RAPL package energy
   (joules per video-hour: the project's end goal), perf-vs-valgrind
   cross-check, and the AVX-512 census tail on Zen 4 / Sapphire
   Rapids before acting on it.
5. **Little-core ARM board** (Pi 3/4, A53/A55 class) — where the
   I-cache discipline and PGO/LTO matter most; validates the
   never-inline-into-decode_b rule against tiny caches. **Apple
   Silicon M1/M2** adds powermetrics energy and a high-IPC ARM
   datapoint. SVE2 (#403): Graviton4/Axion-class for 128-bit SVE2;
   the wider-vector hardware Martin Storsjo awaits barely exists yet.
6. **RISC-V RVV 1.0 board** (Spacemit K1 class, e.g. Banana Pi BPI-F3)
   — the thinnest-asm-coverage target; the pure-C census here is the
   kernel priority list. QEMU validates RVV correctness but not
   performance.
7. **AVX2-less x86** (Atom/Celeron-class) — the real population for
   the new SSSE3 CDEF patches; --cpumask ssse3 on Ice Lake
   approximates the instruction mix but not in-order microarchitecture
   behaviour.

The harness (tests/bench/) and BOOTSTRAP.md are portable to all of
these; only the apt package names and the valgrind availability vary.
