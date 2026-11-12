"""Doctor NoX — read-only FE-XicoOS system-health diagnostics.

Layout:
  model      findings, severities, categories, report schema
  runner     allowlisted, bounded, cancellable subprocess execution
  context    probe environment (fixture-capable filesystem root, env, kernel)
  probes/    diagnostic families, one module per area
  engine     concurrent scan with progress, cancellation and a deadline
  report     text / JSON output and best-effort redaction for sharing
  viewmodel  Qt-free filtering, grouping and detail rendering for the GUI
  gui        PySide6 Qt Widgets desktop application

Nothing in this package changes the system: no privileged helpers, no
service control, no bootc/rpm-ostree transactions, no network requests.
"""
VERSION = "1.0.0"
APP_ID = "io.github.royoshi.doctornox"
