"""patchreach — certify that a workload executes the lines a patch changed.

The public surface is intentionally small:

* :func:`patchreach.diffparse.parse_unified_diff` — read a unified diff into
  the set of added (new-file) line numbers.
* :func:`patchreach.report.build_report` — combine the diff with traced line
  hits into a certificate.
* :func:`patchreach.trace.run_workload` — run a Python entry point under the
  line tracer (used by the CLI child process).

The command line entry point is ``python -m patchreach``.
"""

__version__ = "0.1.0"
