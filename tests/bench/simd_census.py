#!/usr/bin/env python3
# Parse `checkasm --bench` output into a SIMD coverage and speedup census:
#  - kernels with AVX2 but no AVX-512 implementation (gap list)
#  - kernels where AVX-512 barely beats AVX2 (not worth porting elsewhere)
#  - kernels with weak asm-over-C ratios
import re, sys
from collections import defaultdict

TIERS = ["c", "sse2", "ssse3", "sse4", "avx2", "avx512icl"]

def parse(path):
    funcs = defaultdict(dict)
    for line in open(path):
        m = re.match(r"\s*(\S+?)_(c|sse2|ssse3|sse4|avx2|avx512icl):\s+([\d.]+)", line)
        if m:
            funcs[m.group(1)][m.group(2)] = float(m.group(3))
    return funcs

def main():
    funcs = parse(sys.argv[1] if len(sys.argv) > 1 else "/tmp/checkasm-bench.txt")
    hot = sys.argv[2].split(",") if len(sys.argv) > 2 else []

    gaps = [(v["avx2"], f) for f, v in funcs.items()
            if "avx2" in v and "avx512icl" not in v]
    gaps.sort(reverse=True)
    print(f"== kernels with AVX2 but no AVX-512 ({len(gaps)}), by AVX2 cycle cost:")
    for cyc, f in gaps[:25]:
        mark = " <-- HOT in decode profiles" if any(h in f for h in hot) else ""
        print(f"  {cyc:9.1f}  {f}{mark}")

    weak512 = [(v["avx2"] / v["avx512icl"], v["avx2"], f) for f, v in funcs.items()
               if "avx2" in v and "avx512icl" in v]
    weak512.sort()
    print(f"\n== AVX-512 speedup over AVX2 ({len(weak512)} kernels): "
          f"median {sorted(s for s,_,_ in weak512)[len(weak512)//2]:.2f}x")
    print("   weakest (<= 1.05x, AVX-512 not paying):")
    for s, cyc, f in weak512:
        if s <= 1.05:
            print(f"  {s:5.2f}x  avx2={cyc:8.1f}  {f}")

    weakc = [(v["c"] / min(x for k, x in v.items() if k != "c"), v["c"], f)
             for f, v in funcs.items() if "c" in v and len(v) > 1]
    weakc.sort()
    print(f"\n== weakest best-asm-over-C ratios (serial-bound kernels):")
    for s, cyc, f in weakc[:12]:
        print(f"  {s:5.2f}x  c={cyc:8.1f}  {f}")

if __name__ == "__main__":
    main()
