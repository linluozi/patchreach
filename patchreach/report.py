"""Build the reach certificate from a parsed diff and traced line hits."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .diffparse import DiffResult
from .source import FunctionSpan, executable_lines, function_for_line, function_spans

__all__ = ["build_report", "write_report"]


def _read_source(root: str, path: str) -> str | None:
    absolute = os.path.join(root, path)
    try:
        with open(absolute, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _match_profile_span(
    entry: dict[str, Any], spans: list[FunctionSpan]
) -> FunctionSpan | None:
    candidates = [
        span for span in spans if span.co_name == entry.get("name") and span.file == entry.get("file")
    ]
    if not candidates:
        return None
    exact = [span for span in candidates if span.def_line == entry.get("line")]
    if exact:
        return exact[0]
    containing = [
        span
        for span in candidates
        if span.def_line <= int(entry.get("line", 0)) <= span.end_line
    ]
    if containing:
        return max(containing, key=lambda span: span.def_line)
    # Decorated functions: cProfile often reports the decorator's line.
    near = sorted(candidates, key=lambda span: abs(span.def_line - int(entry.get("line", 0))))
    if near and abs(near[0].def_line - int(entry.get("line", 0))) <= 8:
        return near[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _time_ceiling(
    profile: dict[str, Any] | None,
    changed_spans: dict[int, FunctionSpan],
) -> dict[str, Any] | None:
    if profile is None:
        return None
    total_time = float(profile.get("total_time") or 0.0)
    if total_time <= 0:
        return None
    # Best (largest) cumtime per changed function span.  A changed function
    # with no profile row is cold: its cumtime is zero, which is the whole
    # point of the certificate.
    cum_by_id: dict[int, float] = {}
    for entry in profile.get("entries") or []:
        span = _match_profile_span(entry, list(changed_spans.values()))
        if span is None:
            continue
        cumtime = float(entry.get("cumtime") or 0.0)
        cum_by_id[id(span)] = max(cum_by_id.get(id(span), 0.0), cumtime)
    if not changed_spans:
        return {
            "total_time": total_time,
            "changed_functions": 0,
            "profiled_functions": 0,
            "cumtime_share": None,
            "ceiling_upper": None,
        }
    roots: list[FunctionSpan] = []
    for span in changed_spans.values():
        parent = span.parent
        nested = False
        while parent is not None:
            if id(parent) in changed_spans:
                nested = True
                break
            parent = parent.parent
        if not nested:
            roots.append(span)
    share = min(sum(cum_by_id.get(id(span), 0.0) for span in roots) / total_time, 0.999999)
    ceiling = 1.0 / (1.0 - share) if share < 1.0 else None
    return {
        "total_time": total_time,
        "changed_functions": len(changed_spans),
        "profiled_functions": sum(1 for span in changed_spans.values() if id(span) in cum_by_id),
        "root_functions": [span.name for span in roots],
        "cumtime_share": share,
        "ceiling_upper": ceiling,
    }


def build_report(
    diff: DiffResult,
    hits: dict[str, int],
    root: str,
    *,
    profile: dict[str, Any] | None = None,
    min_reach: float = 0.8,
    min_hot: float = 0.5,
    hot_count: int = 10,
    claim: float | None = None,
    workload_exit: int = 0,
    error: str | None = None,
    truncated: bool = False,
    trace_mode: str = "monitoring",
    events: int = 0,
    elapsed_s: float = 0.0,
) -> dict[str, Any]:
    """Return a deterministic, JSON-serialisable certificate."""
    files: list[dict[str, Any]] = []
    function_rows: dict[tuple[str, int, str], dict[str, Any]] = {}
    changed_spans: dict[int, FunctionSpan] = {}
    total_added = 0
    total_executable = 0
    total_reached = 0
    total_hot = 0
    total_hits = 0
    non_executable = 0
    unknown_python = 0
    skipped_non_python = 0

    for path in diff.paths():
        changed = diff.files[path]
        if changed.is_deleted:
            files.append(
                {
                    "path": path,
                    "added": 0,
                    "executable": 0,
                    "reached": 0,
                    "hot": 0,
                    "hits": 0,
                    "deleted": True,
                    "executable_known": True,
                    "unknown_python": 0,
                    "skipped_non_python": 0,
                }
            )
            continue
        source = _read_source(root, path)
        exec_lines = executable_lines(source, path) if source is not None else None
        spans = function_spans(source, path) if source is not None else []
        file_added = len(changed.added)
        file_executable = file_reached = file_hot = file_hits = 0
        file_unknown = file_skipped = 0
        for line in changed.added:
            count = int(hits.get(f"{path}:{line}", 0))
            total_hits += count
            file_hits += count
            if exec_lines is None:
                # A Python file that will not compile is a real unknown; a
                # non-Python file simply has no LINE events to certify.
                if source is not None and path.endswith(".py"):
                    unknown_python += 1
                    file_unknown += 1
                else:
                    skipped_non_python += 1
                    file_skipped += 1
                continue
            if line not in exec_lines:
                non_executable += 1
                continue
            file_executable += 1
            reached = count > 0
            hot = count >= hot_count
            if reached:
                file_reached += 1
            if hot:
                file_hot += 1
            span = function_for_line(spans, line) if spans else None
            if span is not None:
                changed_spans[id(span)] = span
                key = (path, span.def_line, span.name)
                row = function_rows.setdefault(
                    key,
                    {
                        "file": path,
                        "name": span.name,
                        "def_line": span.def_line,
                        "changed": 0,
                        "reached": 0,
                        "hot": 0,
                        "hits": 0,
                    },
                )
                row["changed"] += 1
                row["reached"] += int(reached)
                row["hot"] += int(hot)
                row["hits"] += count
        total_added += file_added
        total_executable += file_executable
        total_reached += file_reached
        total_hot += file_hot
        files.append(
            {
                "path": path,
                "added": file_added,
                "executable": file_executable,
                "reached": file_reached,
                "hot": file_hot,
                "hits": file_hits,
                "deleted": False,
                "executable_known": exec_lines is not None,
                "unknown_python": file_unknown,
                "skipped_non_python": file_skipped,
            }
        )

    reach = (total_reached / total_executable) if total_executable else None
    hot_fraction = (total_hot / total_executable) if total_executable else None

    ceiling = _time_ceiling(profile, changed_spans)

    if error:
        gate = "error"
        passed = False
        reason = "workload failed: " + error.strip().splitlines()[-1]
    elif workload_exit:
        gate = "error"
        passed = False
        reason = f"workload exited with {workload_exit}"
    elif truncated:
        gate = "inconclusive"
        passed = False
        reason = f"trace truncated after {events} events; reach is a lower bound"
    elif total_executable == 0:
        gate = "not-applicable"
        passed = True
        if skipped_non_python:
            reason = "no executable changed Python lines (non-Python files are not traced)"
        else:
            reason = "no executable changed lines (only comments/blank lines or deletions)"
    elif reach is not None and reach < min_reach:
        gate = "fail"
        passed = False
        reason = f"reach {reach:.4f} < min_reach {min_reach:.4f}"
    elif hot_fraction is not None and hot_fraction < min_hot:
        gate = "fail"
        passed = False
        reason = f"hot {hot_fraction:.4f} < min_hot {min_hot:.4f} (hot_count={hot_count})"
    elif claim is not None and ceiling is not None and ceiling.get("ceiling_upper") is not None:
        if claim > float(ceiling["ceiling_upper"]) + 1e-9:
            gate = "fail"
            passed = False
            reason = (
                f"claim {claim:.4f}x > measured upper bound "
                f"{float(ceiling['ceiling_upper']):.4f}x from cumtime share "
                f"{float(ceiling['cumtime_share']):.4f}"
            )
        else:
            gate = "pass"
            passed = True
            reason = "reach/hot thresholds met; claim is not falsified by the time ceiling"
    else:
        gate = "pass"
        passed = True
        reason = "reach/hot thresholds met"

    # An uncompilable Python file means we could not certify its lines.  Never
    # turn that into a silent pass.
    if passed and unknown_python:
        gate = "inconclusive"
        passed = False
        reason = f"{unknown_python} changed Python lines could not be compiled"

    report: dict[str, Any] = {
        "tool": "patchreach",
        "version": "0.1.0",
        "trace_mode": trace_mode,
        "events": events,
        "elapsed_s": round(elapsed_s, 6),
        "truncated": bool(truncated),
        "workload_exit": workload_exit,
        "thresholds": {
            "min_reach": min_reach,
            "min_hot": min_hot,
            "hot_count": hot_count,
            "claim": claim,
        },
        "totals": {
            "added_lines": total_added,
            "executable_lines": total_executable,
            "non_executable_lines": non_executable,
            "unknown_python_lines": unknown_python,
            "skipped_non_python_lines": skipped_non_python,
            "reached_lines": total_reached,
            "hot_lines": total_hot,
            "hits": total_hits,
            "reach": reach,
            "hot_fraction": hot_fraction,
        },
        "files": files,
        "functions": sorted(
            function_rows.values(),
            key=lambda row: (-row["changed"], row["file"], row["def_line"]),
        ),
        "cold_lines": _cold_lines(diff, hits, root),
        "time_ceiling": ceiling,
        "gate": gate,
        "passed": passed,
        "reason": reason,
    }
    canonical_body = {
        key: value
        for key, value in report.items()
        if key not in {"certificate_sha256", "elapsed_s", "events", "time_ceiling"}
    }
    canonical = json.dumps(canonical_body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    report["certificate_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def _cold_lines(diff: DiffResult, hits: dict[str, int], root: str) -> list[dict[str, Any]]:
    """Changed executable lines with zero hits, capped for readability."""
    out: list[dict[str, Any]] = []
    for path in diff.paths():
        changed = diff.files[path]
        if changed.is_deleted:
            continue
        source = _read_source(root, path)
        if source is None:
            continue
        exec_lines = executable_lines(source, path)
        if exec_lines is None:
            continue
        spans = function_spans(source, path)
        for line in changed.added:
            if line not in exec_lines:
                continue
            count = int(hits.get(f"{path}:{line}", 0))
            if count:
                continue
            span = function_for_line(spans, line) if spans else None
            out.append(
                {
                    "file": path,
                    "line": line,
                    "function": span.name if span else None,
                }
            )
            if len(out) >= 40:
                return out
    return out


def write_report(report: dict[str, Any], path: str) -> None:
    """Write a certificate atomically (POSIX rename)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
