# x86 load_tmvs run-find port — WIP, blocked on x86 validation env (2026-06-13)

Porting the AArch64 load_tmvs rework (run-find + span stores + adaptive
selection; see 2026-06-12-load-tmvs-rework.md) to the x86 `sse4` kernel
in `src/x86/refmvs.asm`. This is the highest-value remaining piece:
that kernel serves the entire SSE4+ x86 population (no AVX2 load_tmvs
exists), and it is a structural twin of the pre-rework NEON.

## Toolchain established (works)

- x86-64 cross build on the M4: meson cross file
  (`clang -arch x86_64` + nasm 3.x), `build-x86/`. Builds clean.
- Correctness loop under Rosetta 2: `arch -x86_64 build-x86/tests/checkasm`.
  Output is path-independent (run grouping never changes results), so
  Rosetta correctness is fully valid; perf must come from CI cachegrind.

## Instruction mapping (confirmed in code)

| NEON | x86 | note |
| --- | --- | --- |
| `tbl` mask gather | `pshufb` | per-128-bit lane on x86 |
| `shrn`+`fmov` mask→GPR | **`pmovmskb`** | cheaper on x86 |
| `rbit`+`clz` ffs | `bsf`/`tzcnt` | no `rbit` needed |
| 64-bit GPR run-detect | identical | ports verbatim |
| `cmeq` | `pcmpeqb` | |
| pattern `tbl` chain | `pshufb` + `save_pack0/1` (already in rodata) | |
| projection | unchanged (existing `pmuldq`/`psignd` chain) | source mv from `mvd` not `[rbq]` so run-find can clobber rbq |

## Status by stage

1. **Scalar run-find + interval-clip bulk write** — restructures the
   per-cell write/search into: find run end once, project once,
   write the dx-clipped span without per-cell window tests.
   **VALIDATED: checkasm refmvs 20 seeds pass under Rosetta**
   (before the env failure below). Patch: `tests/bench/wip/x86-scalar-runfind.patch`.
   This is the safe first commit; its standalone perf gain is expected
   to be modest (the big win needs vectorization) and is unmeasured.

2. **Vector run-find (`vff`)** — 16-byte `pcmpeqb [pa] vs [pa+5]` +
   `pmovmskb`, advancing 3 cells (15 bytes, cell-aligned, no division)
   per all-equal chunk; scalar tail pinpoints. **FAILS: deterministic
   checkasm segfault on all 20 seeds.** Logic reviewed as correct and
   read margins computed in-bounds for the checkasm buffer
   (ih8·stride·5 = 75600 B; worst-case `movdqu [pa+5]` end = 75596 B at
   the guard `cells_remaining >= 5`), so the cause is not yet localized
   — likely a subtle x86inc register/`DEFINE_ARGS` aliasing or an
   over-read case the margin analysis misses. Draft:
   `tests/bench/wip/x86-vff-adaptive-wip.diff`.

3. **Adaptive selector** — per-call (n==0) usable-run-extension sample
   sets a `dense` flag (stack slot 0x4c); the xloop forces `xe = x+1`
   (per-cell path) when dense. Drafted in the same diff; not reachable
   until (2) is fixed.

## Blocker: Rosetta validation env wedged

After many `arch -x86_64 checkasm` launches, Rosetta entered a state
where every new launch hangs in uninterruptible sleep (state `UN`, 0
CPU) at startup, accumulating processes that `kill -9` cannot reap.
This killed the local correctness loop mid-debug, so the `vff` segfault
could not be iterated. (The arm64 native build/checkasm is unaffected.)

## How to resume

- Best: a native x86-64 Linux box (or the bench-harness CI x86 leg) as
  the correctness oracle — `build/tests/checkasm` there is authoritative
  and avoids Rosetta entirely. Iterate the `vff` fix against it; perf
  from the CI cachegrind A/B.
- Apply `x86-scalar-runfind.patch` first (known-good) as the baseline
  commit once the full gate + `--cpumask sse4/ssse3` corpus checks pass
  on a working x86 env.
- Then re-introduce `vff` from the diff and bisect the segfault on real
  x86 (where the failure, if real, reproduces without Rosetta noise; if
  it does *not* reproduce, the Rosetta segfault was an artifact and the
  port is already correct).
- On the M4, recovering Rosetta likely needs a logout/reboot to clear
  the stuck processes; run x86 checkasm sparingly (one at a time, with a
  watchdog kill) to avoid re-wedging.
