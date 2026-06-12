#!/usr/bin/env python3
# Benchmark, correctness and profiling driver for dav1d.
#
# Subcommands:
#   make-refs  generate reference MD5s for the local corpus from a trusted
#              baseline build (SIMD and pure-C paths must already agree)
#   check      run conformance vectors against libaom per-frame MD5 ground
#              truth and verify the corpus against stored references, on
#              both the SIMD and pure-C (--cpumask 0) paths
#   bench      wall-clock decode benchmark over the corpus (median of N runs)
#   counters   deterministic instruction/cache/branch counts per clip via
#              valgrind (cachegrind); slow, but stable across runs, so good
#              for comparing builds. These are energy *proxies*, not direct
#              energy measurements.
#   profile    callgrind function-level hotspot profile for one clip
#   compare    diff two bench/counters JSON result files
#
# Results are written as JSON (machine-readable) and printed as a table.

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CORPUS = os.path.join(DATA, "corpus")
CONFORMANCE = os.path.join(DATA, "conformance")
REFS = os.path.join(CORPUS, "refs.json")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def corpus_clips():
    if not os.path.isdir(CORPUS):
        sys.exit(f"no corpus in {CORPUS}; run generate_corpus.sh first")
    return sorted(f for f in os.listdir(CORPUS) if f.endswith(".ivf"))


def clip_info(path):
    """Parse resolution and frame count from the IVF header."""
    with open(path, "rb") as f:
        hdr = f.read(32)
    w = int.from_bytes(hdr[12:14], "little")
    h = int.from_bytes(hdr[14:16], "little")
    n = int.from_bytes(hdr[24:28], "little")
    return w, h, n


def decode_md5(dav1d, path, cpumask=None, threads=0, filmgrain=True):
    cmd = [dav1d, "-q", "-i", path, "--muxer", "md5", "-o", "-",
           "--threads", str(threads)]
    if filmgrain:
        cmd += ["--filmgrain", "1"]
    if cpumask is not None:
        cmd += ["--cpumask", str(cpumask)]
    r = run(cmd)
    if r.returncode != 0:
        return None, r.stderr.strip()
    return r.stdout.strip().split()[0], None


def check_conformance(dav1d, cpumask, threads):
    """Decode each vector to per-frame YUV and compare with libaom's
    per-frame MD5 ground truth."""
    if not os.path.isdir(CONFORMANCE):
        sys.exit(f"no vectors in {CONFORMANCE}; run fetch_conformance.sh")
    vectors = sorted(f for f in os.listdir(CONFORMANCE) if f.endswith(".ivf"))
    failures = []
    for vec in vectors:
        path = os.path.join(CONFORMANCE, vec)
        with open(path + ".md5") as f:
            lines = [line.split() for line in f if line.strip()]
        want = [l[0] for l in lines]
        # frame size from the reference file name, e.g. ...-320x180-0001.i420
        m = re.search(r"-(\d+)x(\d+)-\d+\.i420", lines[0][1])
        w, h = int(m.group(1)), int(m.group(2))
        chroma_px = ((w + 1) // 2) * ((h + 1) // 2)
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [dav1d, "-q", "-i", path, "-o", os.path.join(tmp, "%n.yuv"),
                   "--threads", str(threads)]
            if cpumask is not None:
                cmd += ["--cpumask", str(cpumask)]
            r = run(cmd)
            if r.returncode != 0:
                failures.append((vec, "decode failed: " + r.stderr.strip()))
                continue
            frames = sorted(os.listdir(tmp), key=lambda s: int(s.split(".")[0]))
            got = []
            for fr in frames:
                with open(os.path.join(tmp, fr), "rb") as f:
                    data = f.read()
                # libaom's reference hashes monochrome streams as i420 with
                # neutral gray chroma; dav1d outputs the luma plane only
                if len(data) == w * h:  # 8-bit monochrome
                    data += bytes([128]) * (2 * chroma_px)
                elif len(data) == 2 * w * h:  # high-bitdepth monochrome
                    data += (512).to_bytes(2, "little") * (2 * chroma_px)
                got.append(hashlib.md5(data).hexdigest())
        if got != want:
            failures.append((vec, f"md5 mismatch ({len(got)}/{len(want)} frames)"))
    return len(vectors), failures


def cmd_make_refs(args):
    refs = {}
    for clip in corpus_clips():
        path = os.path.join(CORPUS, clip)
        simd, err = decode_md5(args.dav1d, path)
        if err:
            sys.exit(f"{clip}: decode failed: {err}")
        c, err = decode_md5(args.dav1d, path, cpumask=0)
        if err:
            sys.exit(f"{clip}: C-path decode failed: {err}")
        if simd != c:
            sys.exit(f"{clip}: SIMD/C mismatch at baseline ({simd} vs {c}); "
                     "refusing to write references")
        refs[clip] = simd
        print(f"{clip}: {simd}")
    with open(REFS, "w") as f:
        json.dump(refs, f, indent=2)
    print(f"wrote {REFS}")


def cmd_check(args):
    ok = True
    for cpumask, label in ((None, "simd"), (0, "c")):
        n, failures = check_conformance(args.dav1d, cpumask, args.threads)
        status = "PASS" if not failures else "FAIL"
        print(f"conformance [{label}]: {n - len(failures)}/{n} {status}")
        for vec, why in failures:
            print(f"  {vec}: {why}")
            ok = False
    if os.path.isfile(REFS):
        with open(REFS) as f:
            refs = json.load(f)
        for clip in corpus_clips():
            path = os.path.join(CORPUS, clip)
            for cpumask, label in ((None, "simd"), (0, "c")):
                got, err = decode_md5(args.dav1d, path, cpumask=cpumask,
                                      threads=args.threads)
                if err or got != refs.get(clip):
                    print(f"corpus {clip} [{label}]: FAIL "
                          f"({err or got + ' != ' + refs.get(clip, '?')})")
                    ok = False
        print("corpus verify:", "PASS" if ok else "FAIL")
    else:
        print(f"no {REFS}; skipping corpus verify (run make-refs)")
    sys.exit(0 if ok else 1)


def bench_clip(dav1d, path, threads, runs, limit=None):
    cmd = [dav1d, "-q", "-i", path, "--muxer", "null",
           "--threads", str(threads)]
    if limit:
        cmd += ["-l", str(limit)]
    times = []
    for i in range(runs + 1):  # first run is cache warmup
        t0 = time.perf_counter()
        r = run(cmd)
        dt = time.perf_counter() - t0
        if r.returncode != 0:
            return None
        if i > 0:
            times.append(dt)
    return times


def cmd_bench(args):
    results = {"meta": meta_info(args), "clips": {}}
    rows = []
    for clip in corpus_clips():
        path = os.path.join(CORPUS, clip)
        w, h, frames = clip_info(path)
        entry = {"w": w, "h": h, "frames": frames, "threads": {}}
        for threads in args.threads_list:
            times = bench_clip(args.dav1d, path, threads, args.runs)
            if times is None:
                print(f"{clip}: DECODE FAILED")
                continue
            med = statistics.median(times)
            entry["threads"][threads] = {
                "median_s": round(med, 4),
                "min_s": round(min(times), 4),
                "max_s": round(max(times), 4),
                "fps": round(frames / med, 2),
                "ms_per_frame": round(med * 1000 / frames, 3),
            }
            rows.append((clip, f"{w}x{h}", frames, threads,
                         round(frames / med, 1),
                         round(med * 1000 / frames, 3),
                         round((max(times) - min(times)) / med * 100, 1)))
        results["clips"][clip] = entry
    hdr = ("clip", "res", "frames", "thr", "fps", "ms/frame", "spread%")
    widths = [max(len(str(x)) for x in [h] + [r[i] for r in rows])
              for i, h in enumerate(hdr)]
    print("  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    for r in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(r, widths)))
    save_json(args.out, results)


def parse_cachegrind(outfile):
    """Sum the totals line of a cachegrind output file."""
    events, totals = None, None
    with open(outfile) as f:
        for line in f:
            if line.startswith("events:"):
                events = line.split()[1:]
            elif line.startswith("summary:"):
                totals = [int(x) for x in line.split()[1:]]
    return dict(zip(events, totals))


def cmd_counters(args):
    results = {"meta": meta_info(args), "clips": {}}
    rows = []
    for clip in corpus_clips():
        if args.clip and args.clip not in clip:
            continue
        path = os.path.join(CORPUS, clip)
        w, h, frames = clip_info(path)
        limit = min(frames, args.limit)
        with tempfile.NamedTemporaryFile(suffix=".cg") as tmp:
            cmd = ["valgrind", "--tool=cachegrind", "--cache-sim=yes",
                   "--branch-sim=yes", f"--cachegrind-out-file={tmp.name}",
                   args.dav1d, "-q", "-i", path, "--muxer", "null",
                   "--threads", "1", "-l", str(limit)]
            if args.cpumask is not None:
                cmd += ["--cpumask", str(args.cpumask)]
            r = run(cmd)
            if r.returncode != 0:
                print(f"{clip}: valgrind failed: {r.stderr[-3000:]}")
                continue
            ev = parse_cachegrind(tmp.name)
        per = {k: v / limit for k, v in ev.items()}
        results["clips"][clip] = {"frames": limit, "events": ev,
                                  "per_frame": {k: round(v, 1) for k, v in per.items()}}
        rows.append((clip, limit,
                     f"{per['Ir'] / 1e6:.1f}M",
                     f"{(per['D1mr'] + per['D1mw']) / 1e3:.1f}k",
                     f"{(per['DLmr'] + per['DLmw']) / 1e3:.1f}k",
                     f"{per['Bcm'] / 1e3:.1f}k"))
    hdr = ("clip", "frames", "instr/fr", "D1miss/fr", "LLdmiss/fr", "brmiss/fr")
    widths = [max(len(str(x)) for x in [h] + [r[i] for r in rows])
              for i, h in enumerate(hdr)]
    print("  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    for r in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(r, widths)))
    print("\nnote: instruction/cache/branch counts are energy proxies "
          "(deterministic, single-thread), not direct energy measurements")
    save_json(args.out, results)


def cmd_profile(args):
    clips = [c for c in corpus_clips() if args.clip in c]
    if not clips:
        sys.exit(f"no corpus clip matches '{args.clip}'")
    path = os.path.join(CORPUS, clips[0])
    _, _, frames = clip_info(path)
    limit = min(frames, args.limit)
    out = args.out or f"callgrind.{clips[0]}.out"
    cmd = ["valgrind", "--tool=callgrind", f"--callgrind-out-file={out}",
           args.dav1d, "-q", "-i", path, "--muxer", "null",
           "--threads", "1", "-l", str(limit)]
    if args.cpumask is not None:
        cmd += ["--cpumask", str(args.cpumask)]
    print(" ".join(cmd))
    r = run(cmd)
    if r.returncode != 0:
        sys.exit(f"valgrind failed: {r.stderr[-500:]}")
    ann = run(["callgrind_annotate", "--threshold=97", out])
    print(ann.stdout)
    print(f"full profile in {out}")


def cmd_compare(args):
    with open(args.a) as f:
        a = json.load(f)
    with open(args.b) as f:
        b = json.load(f)
    rows = []
    for clip, ea in sorted(a["clips"].items()):
        eb = b["clips"].get(clip)
        if not eb:
            continue
        if "per_frame" in ea:  # counters comparison
            ia, ib = ea["per_frame"]["Ir"], eb["per_frame"]["Ir"]
            ma = ea["per_frame"]["D1mr"] + ea["per_frame"]["D1mw"]
            mb = eb["per_frame"]["D1mr"] + eb["per_frame"]["D1mw"]
            rows.append((clip, f"{(ib / ia - 1) * 100:+.2f}%",
                         f"{(mb / ma - 1) * 100:+.2f}%"))
            hdr = ("clip", "instr/frame", "D1miss/frame")
        else:  # bench comparison
            for thr, ta in ea["threads"].items():
                tb = eb["threads"].get(thr)
                if tb:
                    rows.append((f"{clip} thr={thr}",
                                 f"{(tb['fps'] / ta['fps'] - 1) * 100:+.2f}%",
                                 f"{(tb['ms_per_frame'] / ta['ms_per_frame'] - 1) * 100:+.2f}%"))
            hdr = ("clip", "fps", "ms/frame")
    widths = [max(len(str(x)) for x in [h] + [r[i] for r in rows])
              for i, h in enumerate(hdr)]
    print("  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    for r in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def meta_info(args):
    git = run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"])
    lib = os.path.join(os.path.dirname(os.path.dirname(args.dav1d)),
                       "src", "libdav1d.so.7.0.0")
    return {
        "commit": git.stdout.strip(),
        "dav1d": args.dav1d,
        "libsize": os.path.getsize(lib) if os.path.isfile(lib) else None,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_json(out, results):
    if out:
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nresults written to {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dav1d", default=os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "build", "tools", "dav1d"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("make-refs")

    c = sub.add_parser("check")
    c.add_argument("--threads", type=int, default=0)

    b = sub.add_parser("bench")
    b.add_argument("--runs", type=int, default=5)
    b.add_argument("--threads", default="1,4",
                   help="comma-separated thread counts")
    b.add_argument("--out", help="write JSON results here")

    n = sub.add_parser("counters")
    n.add_argument("--limit", type=int, default=60,
                   help="max frames per clip (valgrind is ~30x slower)")
    n.add_argument("--clip", help="only clips whose name contains this")
    n.add_argument("--cpumask", type=int, default=None,
                   help="e.g. 0 to measure the pure-C path")
    n.add_argument("--out", help="write JSON results here")

    f = sub.add_parser("profile")
    f.add_argument("clip", help="substring of corpus clip name")
    f.add_argument("--limit", type=int, default=60)
    f.add_argument("--cpumask", type=int, default=None)
    f.add_argument("--out")

    d = sub.add_parser("compare")
    d.add_argument("a")
    d.add_argument("b")

    args = p.parse_args()
    if args.cmd == "bench":
        args.threads_list = [int(t) for t in args.threads.split(",")]
    {"make-refs": cmd_make_refs, "check": cmd_check, "bench": cmd_bench,
     "counters": cmd_counters, "profile": cmd_profile,
     "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
