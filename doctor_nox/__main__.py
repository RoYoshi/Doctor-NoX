"""`python3 -m doctor_nox` runs the same command line as `doctor-nox`."""
import sys

from .cli import main

sys.exit(main())
