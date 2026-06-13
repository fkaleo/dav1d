# x86 load_tmvs run-find port (2026-06-13)

Porting the AArch64 load_tmvs rework (run-find + span stores + adaptive
selection; see 2026-06-12-load-tmvs-rework.md) to the x86 `sse4` kernel
in `src/x86/refmvs.asm`. That kernel serves the entire SSE4+ x86
population (no AVX2 load_tmvs exists) and is a structural twin of the
pre-rework NEON.

## Landed: vectorized run-find + span stores

The per-cell write/search loops are replaced with run-level processing:

- **Run-find**: a run is a span where `cell[j] == cell[j+1]`, i.e. data
  byte `k == k+5`. A vector fast-forward compares `[pa]` vs `[pa+5]`
  (`pcmpeqb`/`pmovmskb`); bits 0-14 cover three cells against their
  successors, so all-set advances 3 cells (15 bytes — cell-aligned, no
  division). A scalar tail pinpoints the end. **Guard `cells_remaining
  >= 5`** keeps the `+5` load (which touches cell `scur+4`) inside the
  buffer — `>= 4` over-reads one cell past the allocation at the last
  row/column (a real segfault; see below).
- **Span store**: project once per run, then write the dx-clipped
  interval `[max(x, xstart-dx), min(xe, xend-dx))` without per-cell
  window tests. `pshufb` pattern tiling via the existing `save_pack0/1`
  rodata; the projection now sources the mv from the `mvd` register
  (the run-find clobbers `rbq`).

Validated: **checkasm refmvs, 20 seeds, all pass under Rosetta** (M4
x86-64 cross build, nasm), and the **bench-harness CI x86-64 leg is
green** — conformance 22/22 + corpus bit-exact + checkasm on real
x86-64. So the port is correctness-validated on native hardware.

**Perf measurement (the honest part).** The CI cachegrind A/B shows the
x86 screen-1080p instruction count **unchanged** by this commit
(10.319M/frame branch, identical before and after; −3.80% vs master is
entirely the C-path changes). That is expected, not a failure: as the
NEON work established, this loop's cost is **branches and load→compare
latency, not instruction count** — the NEON −55% on screen was M4
interleaved *wall-clock*, and it never moved cachegrind `Ir` either.
cachegrind here measures `Ir`/`D1` only, over 20-frame clips where
load_tmvs is a small slice, so it cannot see a cycle-level win. The x86
benefit therefore is **not demonstrable with the available tooling**
(no native-x86 `perf`/cycle counters; Rosetta timings invalid). It is
correct and perf-neutral on the measurable metric (no regression: `Ir`
and `D1` unchanged), and is the x86 analogue of a wall-clock-proven
NEON win.

Recommendation: keep it as the validated port foundation; demonstrate
(or refute) the x86 cycle win on a native x86-64 box with `perf stat`
on a long screen clip, the same way the NEON win was shown by M4
wall-clock. This mirrors the project's standing rule that small-delta
wall-clock claims belong on bare metal, not in this counter harness.

## Deferred: adaptive dense selector

The per-call usable-run-extension sample + `dense` gate (NEON round 3,
which turns the dense-content regression into a win) is **not yet
landed**: its dense path (`xe = x+1; jmp .project`) hits a checkasm
segfault that is still being chased. Without it the x86 kernel has the
same dense-content regression NEON had after rounds 1-2 (a synthetic
worst case; real content wins) — an acceptable interim state. The NEON
version (`src/arm/64/refmvs.S`) is the reference for re-deriving it.

## Debugging lessons (cost real time; recorded for next time)

1. **Unsafe stack slot.** The original segfault hunt was a wild goose
   chase: `[rsp+0x4c]` is **not** a safe local in this `cglobal ...,
   -0x50` frame (existing code only uses up to `0x48`); writing it
   corrupts the stack. The `dense` flag and sample scratch must live in
   a gap within `[0x00, 0x48]` (e.g. `0x34`). This masqueraded as a
   "vff bug" and a "sample bug" because every variant that touched
   `0x4c` crashed.
2. **Rosetta wedges under load.** Many rapid `arch -x86_64 checkasm`
   launches drive Rosetta into a state where new launches hang in
   uninterruptible sleep (state `UN`, 0 CPU) and accumulate unkillable
   processes; it recovers on its own after a pause. Run x86 checkasm
   **one at a time, with a watchdog kill**, and treat a hang as an env
   artifact, not a code failure. Stale binaries (ninja not relinking
   between edits) also produced misleading pass/fail readings — always
   confirm the rebuild.
3. The vff is correct; the read-margin guard is the only subtlety
   (`>= 5`, derived above).

## To finish

- Land via the CI x86 leg (real x86-64): correctness gate + corpus +
  `--cpumask sse4/ssse3` + the cachegrind A/B perf number. Watch the
  bench-harness run on push and revert if red.
- Re-add the adaptive selector at a safe slot (`0x34`) and debug the
  dense path on native x86 (no Rosetta noise).
- An AVX2 `load_tmvs` is a possible follow-up, but the win is the
  algorithm (per the NEON data), not vector width.
