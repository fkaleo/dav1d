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

*Correction (2026-06-14):* the original landing commit `dc61967a` only
staged the docs/wip cleanup — `src/x86/refmvs.asm` was never committed,
so that run's green tested the **unchanged** upstream kernel. The asm
was actually landed in `6fa73fcc`; the green referenced above is the
CI run on that commit, which is the first one that genuinely exercises
the change.

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

## Landed: adaptive dense selector (2026-06-14)

The per-call usable-run-extension sample + `dense` gate (NEON round 3)
**landed in `3402cbad`** (checkasm refmvs, 20 seeds). The dense-path
"segfault" referenced earlier was never a code bug — it was the same
stack-slot aliasing as the original `0x4c` hunt: the dense flag at
`0x34` overwrites the **high 32 bits of the `stride` qword at `0x30`**,
so `dense=1` set `stride = 0x1_000000f0` and the span wrote to a wild
dst (lldb: faulting dst, then the corrupted `[rsp+0x30]`). The function
packs qword locals (rf@0x48, stride@0x30) next to dwords, so **no dword
slot inside `[0x00,0x4f]` that lands in a qword's high half is safe**.
Fixed by growing the frame to `-0x60` and using genuinely-free dword
slots `0x50/0x54/0x58`. NEON energy: adaptive −0.7% vs run-level on the
dense stressor (small at whole-decoder; load_tmvs is ~2.4% there).

## Attempted, deferred: skip-scan (phase A, pshufb-gather)

The vectorized skip of unusable cells (NEON phase A) was implemented:
5 `pshufb` masks gather the stride-5 ref bytes of 16 cells, a per-`n`
`pshufb` usable table maps them, `pmovmskb`+`tzcnt` find the first
usable cell; scalar fallback when <16 cells remain. It passes **18/20
checkasm seeds** but two (1234, 31337) give a wrong result (not a
segfault) — a subtle edge case the gather/table review did not catch,
and checkasm in this build does not print the mismatch coordinates to
localize it. **Deferred**, because (a) it needs a debugger that shows
the mismatched cell (or a native x86 run) to crack, and (b) its benefit
is unmeasurable here anyway (cycle-level, no native-x86 `perf`). The
WIP is saved as a patch in the session tmp
(`x86-skipscan-wip.patch`); the run-find + span + adaptive that ship
are the validated, valuable part. Without phase A, x86 still skips
unusable cells per-cell (as upstream did).

## Debugging lessons (cost real time; recorded for next time)

1. **Unsafe stack slot (×2).** The function packs qword locals next to
   dwords; a dword flag that lands in a qword's **high half** silently
   corrupts that qword. `0x4c` overlaps `rf`@`0x48`; `0x34` overlaps
   `stride`@`0x30`. Both produced wild-pointer segfaults that
   masqueraded as "vff bug" / "sample bug" / "dense-path bug". The fix
   is to grow the frame (`-0x60`) and use slots that don't overlap any
   qword (`0x50/0x54/0x58`). Lesson: before reusing a stack gap, check
   it isn't the high half of an 8-byte local.
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

- Debug the skip-scan's 2/20 wrong-result on a native x86-64 box (or a
  build whose checkasm prints the mismatched cell) — apply
  `x86-skipscan-wip.patch` to the committed kernel. The masks and table
  reviewed as correct, so the bug is likely a boundary in the skip16
  loop or an off-by-one in the window, not the gather.
- Quantify the run-find/adaptive (and, once fixed, skip-scan) cycle win
  on native x86-64 with `perf stat` — cachegrind `Ir` cannot see it,
  and the NEON energy result (−10.3% screen) is the analogue to confirm.
- An AVX2 `load_tmvs` is a possible follow-up, but the win is the
  algorithm (per the NEON data), not vector width.
