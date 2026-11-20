"""Run a Python workload under a line tracer and (optionally) cProfile.

The tracer only records files named in the patch.  It is deliberately run in
a child process so that the harness's own imports and allocations never enter
the certificate.
"""

from __future__ import annotations

import cProfile
import os
import pstats
import runpy
import sys
import time
import traceback
from typing import Any, Callable

__all__ = ["run_workload", "target_map"]


def target_map(root: str, paths: list[str]) -> dict[str, str]:
    """Map ``realpath`` of each changed file to its POSIX display path."""
    out: dict[str, str] = {}
    for path in paths:
        absolute = os.path.realpath(os.path.join(root, path))
        out[absolute] = path.replace(os.sep, "/")
    return out


def _make_workload(script: str | None, module: str | None, args: list[str]) -> Callable[[], None]:
    def run() -> None:
        if module:
            sys.argv = [module] + list(args)
            runpy.run_module(module, run_name="__main__", alter_sys=True)
            return
        assert script is not None
        script_abs = os.path.abspath(script)
        sys.argv = [script_abs] + list(args)
        script_dir = os.path.dirname(script_abs)
        inserted = False
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)
            inserted = True
        try:
            runpy.run_path(script_abs, run_name="__main__")
        finally:
            if inserted:
                try:
                    sys.path.remove(script_dir)
                except ValueError:
                    pass

    return run


def _exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    return 0 if exc.code is None else 1


def _run_monitoring(
    targets: dict[str, str], max_events: int, run: Callable[[], None]
) -> tuple[dict[str, int], dict[str, Any], str | None, int]:
    monitoring = sys.monitoring
    tool_id = None
    for candidate in (4, 3, 2, 5, 1, 0):
        try:
            monitoring.use_tool_id(candidate, "patchreach")
            tool_id = candidate
            break
        except ValueError:
            continue
    if tool_id is None:
        raise RuntimeError("no free sys.monitoring tool id")

    hits: dict[str, int] = {}
    state: dict[str, Any] = {"events": 0, "truncated": False}
    filename_cache: dict[str, str | None] = {}
    missing = object()

    def callback(code: Any, line: int) -> None:
        if state["truncated"]:
            return
        filename = code.co_filename
        display = filename_cache.get(filename, missing)
        if display is missing:
            if not filename or filename.startswith("<"):
                display = None
            else:
                display = targets.get(os.path.realpath(filename))
            filename_cache[filename] = display  # type: ignore[assignment]
        if display is None:
            return
        key = f"{display}:{line}"
        hits[key] = hits.get(key, 0) + 1
        state["events"] += 1
        if max_events and state["events"] >= max_events:
            state["truncated"] = True

    error: str | None = None
    exit_code = 0
    try:
        monitoring.register_callback(tool_id, monitoring.events.LINE, callback)
        monitoring.set_events(tool_id, monitoring.events.LINE)
        try:
            run()
        except SystemExit as exc:
            exit_code = _exit_code(exc)
        except BaseException:
            error = traceback.format_exc(limit=8)
            exit_code = 1
    finally:
        try:
            monitoring.set_events(tool_id, 0)
        except Exception:
            pass
        try:
            monitoring.register_callback(tool_id, monitoring.events.LINE, None)
        except Exception:
            pass
        try:
            monitoring.free_tool_id(tool_id)
        except Exception:
            pass
    return hits, state, error, exit_code


def _run_settrace(
    targets: dict[str, str], max_events: int, run: Callable[[], None]
) -> tuple[dict[str, int], dict[str, Any], str | None, int]:
    hits: dict[str, int] = {}
    state: dict[str, Any] = {"events": 0, "truncated": False}
    filename_cache: dict[str, str | None] = {}

    def resolve(filename: str) -> str | None:
        if filename in filename_cache:
            return filename_cache[filename]
        if not filename or filename.startswith("<"):
            display = None
        else:
            display = targets.get(os.path.realpath(filename))
        filename_cache[filename] = display
        return display

    def local(frame: Any, event: str, arg: Any) -> Any:
        if event == "line" and not state["truncated"]:
            display = resolve(frame.f_code.co_filename)
            if display is not None:
                key = f"{display}:{frame.f_lineno}"
                hits[key] = hits.get(key, 0) + 1
                state["events"] += 1
                if max_events and state["events"] >= max_events:
                    state["truncated"] = True
        return local

    def global_trace(frame: Any, event: str, arg: Any) -> Any:
        if event != "call":
            return None
        return local if resolve(frame.f_code.co_filename) is not None else None

    error: str | None = None
    exit_code = 0
    previous = sys.gettrace()
    sys.settrace(global_trace)
    try:
        try:
            run()
        except SystemExit as exc:
            exit_code = _exit_code(exc)
        except BaseException:
            error = traceback.format_exc(limit=8)
            exit_code = 1
    finally:
        sys.settrace(previous)
    return hits, state, error, exit_code


def _run_profile(
    targets: dict[str, str], run: Callable[[], None]
) -> tuple[dict[str, Any] | None, str | None, int]:
    profiler = cProfile.Profile()
    error: str | None = None
    exit_code = 0
    profiler.enable()
    try:
        run()
    except SystemExit as exc:
        exit_code = _exit_code(exc)
    except BaseException:
        error = traceback.format_exc(limit=8)
        exit_code = 1
    finally:
        profiler.disable()
    stats = pstats.Stats(profiler)
    entries: list[dict[str, Any]] = []
    for (filename, lineno, name), values in stats.stats.items():
        _cc, nc, tottime, cumtime, _callers = values
        if not filename or filename.startswith("<") or filename == "~":
            continue
        display = targets.get(os.path.realpath(filename))
        if display is None:
            continue
        entries.append(
            {
                "file": display,
                "line": lineno,
                "name": name,
                "ncalls": nc,
                "tottime": tottime,
                "cumtime": cumtime,
            }
        )
    entries.sort(key=lambda item: item["cumtime"], reverse=True)
    return (
        {"total_time": float(getattr(stats, "total_tt", 0.0)), "entries": entries},
        error,
        exit_code,
    )


def run_workload(
    root: str,
    *,
    script: str | None = None,
    module: str | None = None,
    args: list[str] | None = None,
    changed_paths: list[str] | None = None,
    time_profile: bool = False,
    max_events: int = 5_000_000,
) -> dict[str, Any]:
    """Trace (and optionally profile) ``script`` or ``module``.

    ``changed_paths`` are paths relative to ``root`` as they appear in the
    diff.  Only those files contribute hits.
    """
    args = list(args or [])
    changed_paths = list(changed_paths or [])
    targets = target_map(root, changed_paths)
    run = _make_workload(script, module, args)

    started = time.perf_counter()
    try:
        hits, state, error, exit_code = _run_monitoring(targets, max_events, run)
    except (AttributeError, RuntimeError):
        hits, state, error, exit_code = _run_settrace(targets, max_events, run)

    result: dict[str, Any] = {
        "trace_mode": "monitoring" if hasattr(sys, "monitoring") else "settrace",
        "hits": hits,
        "truncated": state["truncated"],
        "events": state["events"],
        "elapsed_s": time.perf_counter() - started,
        "workload_exit": exit_code,
        "error": error,
        "profile": None,
    }

    if time_profile:
        try:
            profile, profile_error, profile_exit = _run_profile(targets, run)
            result["profile"] = profile
            if profile_error and not result["error"]:
                result["error"] = profile_error
            if profile_exit and not result["workload_exit"]:
                result["workload_exit"] = profile_exit
        except SystemExit:
            pass
        except BaseException:
            result["profile"] = None
            result["error"] = (result["error"] or "") + "\nprofile run failed:\n" + traceback.format_exc(limit=8)

    return result
