# x86 load_tmvs port — work in progress

See ../reports/2026-06-13-x86-load-tmvs-port.md for the full writeup.

- `x86-scalar-runfind.patch` — scalar run-find + interval-clip bulk
  write for the sse4 load_tmvs. VALIDATED (checkasm refmvs, 20 seeds).
  Safe first commit; apply with `git apply` from the repo root after
  the full gate passes on a working x86 env.
- `x86-vff-adaptive-wip.diff` — adds the vectorized run-find (pcmpeqb/
  pmovmskb) and the adaptive dense selector. DRAFT: deterministic
  checkasm segfault not yet localized; do not apply without debugging
  on a native x86-64 checkasm.
