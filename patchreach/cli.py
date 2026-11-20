"""Command line interface for patchreach."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import __version__
from .diffparse import DiffParseError, read_patch
from .report import build_report, write_report
from .source import executable_lines, function_for_line, function_spans
from .trace import run_workload

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_WORKLOAD = 3
EXIT_INCONCLUSIVE = 4


def _pkg_parent() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _strip_dash(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        diff = read_patch(args.patch)
    except (OSError, DiffParseError) as exc:
        print(f"patchreach: cannot read patch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not diff.paths():
        print("patchreach: patch contains no file changes", file=sys.stderr)
        return EXIT_USAGE
    if not args.script and not args.module:
        print("patchreach: check needs --script or --module", file=sys.stderr)
        return EXIT_USAGE

    root = os.path.abspath(args.root)
    out_path = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        f"patchreach-trace-{os.getpid()}.json",
    )
    cmd = [
        sys.executable,
        "-m",
        "patchreach",
        "_trace",
        "--root",
        root,
        "--out",
        out_path,
        "--max-events",
        str(args.max_events),
    ]
    if args.script:
        cmd += ["--script", args.script]
    else:
        cmd += ["--module", args.module]
    for path in diff.paths():
        cmd += ["--changed", path]
    if args.time_profile:
        cmd += ["--time"]
    cmd += ["--"] + _strip_dash(list(args.workload_args))

    env = dict(os.environ)
    parent = _pkg_parent()
    existing = env.get("PYTHONPATH", "")
    if parent not in existing.split(os.pathsep):
        env["PYTHONPATH"] = (existing + os.pathsep + parent) if existing else parent
    try:
        completed = subprocess.run(
            cmd,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"patchreach: workload timed out after {args.timeout}s", file=sys.stderr)
        return EXIT_WORKLOAD
    finally:
        pass
    if args.verbose and completed.stdout:
        sys.stdout.write(completed.stdout)
    if args.verbose and completed.stderr:
        sys.stderr.write(completed.stderr)

    try:
        with open(out_path, "r", encoding="utf-8") as fh:
            trace = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"patchreach: tracer produced no report ({exc})", file=sys.stderr)
        if completed.stderr:
            sys.stderr.write(completed.stderr[-2000:])
        return EXIT_WORKLOAD
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    report = build_report(
        diff,
        trace.get("hits") or {},
        root,
        profile=trace.get("profile"),
        min_reach=args.min_reach,
        min_hot=args.min_hot,
        hot_count=args.hot_count,
        claim=args.claim,
        workload_exit=int(trace.get("workload_exit") or 0),
        error=trace.get("error"),
        truncated=bool(trace.get("truncated")),
        trace_mode=str(trace.get("trace_mode") or "unknown"),
        events=int(trace.get("events") or 0),
        elapsed_s=float(trace.get("elapsed_s") or 0.0),
    )
    if args.json_out:
        write_report(report, args.json_out)
    _print_report(report, args.patch, args)
    if report["gate"] in ("pass", "not-applicable"):
        return EXIT_PASS
    if report["gate"] in ("error",):
        return EXIT_WORKLOAD
    if report["gate"] in ("inconclusive",):
        return EXIT_INCONCLUSIVE
    return EXIT_FAIL


def _print_report(report: dict, patch: str, args: argparse.Namespace) -> None:
    totals = report["totals"]
    print("patchreach %s — diff-scoped reach certificate" % __version__)
    workload = args.script or f"-m {args.module}"
    print(f"patch: {patch}    workload: {workload}")
    print(
        "trace: %s, %d events, %.3fs"
        % (report["trace_mode"], report["events"], report["elapsed_s"])
    )
    print()
    header = f"{'file':<38} {'added':>5} {'exec':>5} {'reach':>5} {'hot':>4} {'hits':>7}"
    print(header)
    print("-" * len(header))
    for row in report["files"]:
        if row.get("deleted"):
            print(f"{row['path']:<38} {'deleted':>5}")
            continue
        print(
            f"{row['path']:<38} {row['added']:>5} {row['executable']:>5} "
            f"{row['reached']:>5} {row['hot']:>4} {row['hits']:>7}"
        )
    print()
    reach = totals["reach"]
    hot = totals["hot_fraction"]
    print(
        "totals: executable=%d reached=%d hot=%d hits=%d reach=%s hot=%s"
        % (
            totals["executable_lines"],
            totals["reached_lines"],
            totals["hot_lines"],
            totals["hits"],
            "n/a" if reach is None else f"{reach:.4f}",
            "n/a" if hot is None else f"{hot:.4f}",
        )
    )
    if totals["non_executable_lines"]:
        print(f"non-executable changed lines (comments/blank): {totals['non_executable_lines']}")
    if totals.get("unknown_python_lines"):
        print(f"changed Python lines in files that could not be compiled: {totals['unknown_python_lines']}")
    if totals.get("skipped_non_python_lines"):
        print(f"changed non-Python lines (not traced): {totals['skipped_non_python_lines']}")
    cold = report.get("cold_lines") or []
    if cold:
        shown = ", ".join(
            f"{row['file']}:{row['line']}" + (f" ({row['function']})" if row.get("function") else "")
            for row in cold[:10]
        )
        print(f"cold changed lines: {shown}" + (" ..." if len(cold) > 10 else ""))
    ceiling = report.get("time_ceiling")
    if ceiling and ceiling.get("cumtime_share") is not None:
        print(
            "time ceiling: changed functions cumtime share %.4f -> upper bound %.3fx"
            % (ceiling["cumtime_share"], ceiling["ceiling_upper"] or 0.0)
        )
    print()
    print(f"GATE: {report['gate'].upper()} — {report['reason']}")
    print(f"certificate sha256: {report['certificate_sha256']}")


def _cmd_explain(args: argparse.Namespace) -> int:
    try:
        diff = read_patch(args.patch)
    except (OSError, DiffParseError) as exc:
        print(f"patchreach: cannot read patch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    root = os.path.abspath(args.root)
    records = []
    for path in diff.paths():
        changed = diff.files[path]
        source = None
        absolute = os.path.join(root, path)
        try:
            with open(absolute, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError:
            pass
        exec_lines = executable_lines(source, path) if source is not None else None
        spans = function_spans(source, path) if source is not None else []
        lines = []
        for line in changed.added:
            span = function_for_line(spans, line) if spans else None
            lines.append(
                {
                    "line": line,
                    "executable": None if exec_lines is None else line in exec_lines,
                    "function": span.name if span else None,
                }
            )
        records.append(
            {
                "path": path,
                "added": changed.added,
                "removed": changed.removed,
                "hunks": changed.hunks,
                "is_new": changed.is_new,
                "is_deleted": changed.is_deleted,
                "executable_known": exec_lines is not None,
                "lines": lines,
            }
        )
    payload = {"patch": args.patch, "files": records}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
    print("patchreach %s — changed-line map" % __version__)
    for rec in records:
        print()
        print(f"{rec['path']}  (+{len(rec['added'])} -{rec['removed']}, {rec['hunks']} hunks)")
        if rec["is_deleted"]:
            print("  (deleted; no new lines to trace)")
            continue
        if not rec["executable_known"]:
            print("  (could not compile; executable set unknown)")
        for item in rec["lines"]:
            flag = "exec" if item["executable"] else ("non-exec" if item["executable"] is False else "unknown")
            func = item["function"] or "<module>"
            print(f"  line {item['line']:>5}  {flag:<8} {func}")
    return EXIT_PASS


def _cmd_trace(args: argparse.Namespace) -> int:
    try:
        payload = run_workload(
            os.path.abspath(args.root),
            script=args.script,
            module=args.module,
            args=_strip_dash(list(args.workload_args)),
            changed_paths=list(args.changed),
            time_profile=args.time_profile,
            max_events=args.max_events,
        )
    except BaseException as exc:  # never leave the parent without a report
        payload = {
            "trace_mode": "none",
            "hits": {},
            "truncated": False,
            "events": 0,
            "elapsed_s": 0.0,
            "workload_exit": 1,
            "error": f"{type(exc).__name__}: {exc}",
            "profile": None,
        }
    try:
        write_report(payload, args.out)
    except OSError as exc:
        print(f"patchreach: cannot write trace: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_PASS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchreach",
        description="Certify that a workload executes the lines a patch changed.",
    )
    parser.add_argument("--version", action="version", version=f"patchreach {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="trace a workload against a patch and gate on reach")
    check.add_argument("--patch", required=True, help="unified diff to inspect")
    check.add_argument("--root", default=".", help="repository root the patch applies to")
    group = check.add_mutually_exclusive_group(required=True)
    group.add_argument("--script", help="Python script to run as __main__")
    group.add_argument("--module", help="Python module to run with runpy -m")
    check.add_argument("--min-reach", type=float, default=0.80, help="required fraction of executable changed lines reached")
    check.add_argument("--min-hot", type=float, default=0.50, help="required fraction of executable changed lines executed >= hot-count times")
    check.add_argument("--hot-count", type=int, default=10, help="executions needed for a line to count as hot")
    check.add_argument("--claim", type=float, default=None, help="claimed overall speedup; checked against the cumtime upper bound")
    check.add_argument("--time", action="store_true", dest="time_profile", help="also run once under cProfile for the Amdahl upper bound")
    check.add_argument("--max-events", type=int, default=5_000_000, help="stop counting after this many line events")
    check.add_argument("--json", dest="json_out", default=None, help="write the certificate JSON here")
    check.add_argument("--timeout", type=float, default=120.0, help="wall-clock timeout for the workload")
    check.add_argument("--verbose", action="store_true", help="echo the workload's stdout/stderr")
    check.add_argument("workload_args", nargs=argparse.REMAINDER, help="args after -- go to the workload")

    explain = sub.add_parser("explain", help="list changed lines and whether they are executable")
    explain.add_argument("--patch", required=True)
    explain.add_argument("--root", default=".")
    explain.add_argument("--json", dest="json_out", default=None)

    trace = sub.add_parser("_trace", help=argparse.SUPPRESS)
    trace.add_argument("--root", required=True)
    trace.add_argument("--out", required=True)
    trace.add_argument("--script", default=None)
    trace.add_argument("--module", default=None)
    trace.add_argument("--changed", action="append", default=[])
    trace.add_argument("--time", action="store_true", dest="time_profile")
    trace.add_argument("--max-events", type=int, default=5_000_000)
    trace.add_argument("workload_args", nargs=argparse.REMAINDER)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "explain":
        return _cmd_explain(args)
    if args.command == "_trace":
        return _cmd_trace(args)
    parser.error("unknown command")
    return EXIT_USAGE
