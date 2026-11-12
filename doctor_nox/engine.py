"""Run all probes concurrently with isolation, progress, cancellation and a deadline."""
from __future__ import annotations

import datetime
import queue
import threading
import time
from typing import Callable

from . import probes as probe_pkg
from .context import Context
from .model import CATEGORIES, UNKNOWN, Finding, build_report

Progress = Callable[[int, int, str], None]
DEFAULT_DEADLINE = 60.0


def _as_list(value):
    if isinstance(value, Finding):
        return [value]
    if isinstance(value, list) and all(isinstance(v, Finding) for v in value):
        return value
    raise TypeError("probe returned something other than Finding(s)")


def _failure(p, summary, reason):
    return Finding(id=p.id, category=p.category, title=p.title, severity=UNKNOWN,
                   summary=summary, reason=reason[:300])


def scan(ctx: Context | None = None, *, progress: Progress | None = None,
         deadline: float = DEFAULT_DEADLINE, selected: set[str] | None = None,
         probes=None, workers: int = 4) -> dict:
    """Return a report dict. Never raises for probe failures; never mutates the host."""
    ctx = ctx or Context()
    all_probes = probes if probes is not None else probe_pkg.load_all()
    todo = [p for p in all_probes if not selected or p.id in selected or p.category in selected]
    started = datetime.datetime.now(datetime.timezone.utc)
    t0 = time.monotonic()
    results: dict[str, list[Finding]] = {}
    jobs: queue.Queue = queue.Queue()
    done_q: queue.Queue = queue.Queue()
    for p in todo:
        jobs.put(p)

    def worker():
        # Daemon threads: a probe stuck in an uninterruptible call (e.g. statvfs on a hung
        # network mount) must never keep the CLI or the GUI process alive after the scan.
        while not ctx.cancel.is_set():
            try:
                p = jobs.get_nowait()
            except queue.Empty:
                return
            try:
                found = _as_list(p.func(ctx))
            except Exception as exc:  # isolation: one broken probe never sinks the scan
                found = [_failure(p, "Diagnostic could not complete", f"{type(exc).__name__}: {exc}")]
            done_q.put((p, found))

    timer = threading.Timer(deadline, ctx.cancel.set)
    timer.daemon = True
    timer.start()
    threads = [threading.Thread(target=worker, name=f"doctor-probe-{i}", daemon=True)
               for i in range(max(1, min(workers, len(todo))))]
    interrupted = True  # stays True if the loop is left by an exception
    try:
        for t in threads:
            t.start()
        done = 0
        grace_until = None
        while done < len(todo):
            if ctx.cancel.is_set() and grace_until is None:
                grace_until = time.monotonic() + 3.0  # in-flight commands are killed; let probes return
            if grace_until is not None and time.monotonic() >= grace_until:
                break
            try:
                p, found = done_q.get(timeout=0.1)
            except queue.Empty:
                if not any(t.is_alive() for t in threads) and done_q.empty():
                    break  # workers stopped after cancellation; remaining probes never started
                continue
            results[p.id] = found
            done += 1
            if progress:
                progress(done, len(todo), p.title)
        interrupted = ctx.cancel.is_set()  # cancelled/deadline before every probe reported
    finally:
        timer.cancel()
        ctx.cancel.set()  # stop anything still running (also on KeyboardInterrupt / callback errors)
    for p in todo:
        if p.id not in results:
            results[p.id] = [_failure(p, "Not completed: scan cancelled",
                                      "The scan was cancelled or hit its time limit before this check finished.")]
    cancelled = interrupted
    order = {key: i for i, (key, _) in enumerate(CATEGORIES)}
    findings = []
    for p in todo:
        findings.extend(results[p.id])
    findings.sort(key=lambda f: order[f.category])  # stable: keeps registry order inside a category
    _dedupe(findings)
    from .probes.system import host_summary
    try:
        host = host_summary(ctx)
    except Exception:  # identity must never crash the report
        host = {"name": "unknown", "kernel": ctx.kernel}
    finished_at = started + datetime.timedelta(seconds=time.monotonic() - t0)
    return build_report(findings, host, started=started, finished=finished_at, complete=not cancelled)


def _dedupe(findings):
    seen = {}
    for f in findings:
        if f.id in seen:
            seen[f.id] += 1
            f.id = f"{f.id}.dup{seen[f.id]}"
        else:
            seen[f.id] = 0
