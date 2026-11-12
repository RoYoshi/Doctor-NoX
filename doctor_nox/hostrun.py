"""Doctor NoX's command line on the host, for the Flatpak build.

The Flatpak starts it with the host's own Python, from the app's folder:

    flatpak-spawn --host /usr/bin/python3 -I -B .../share/doctor-nox/doctor_nox/hostrun.py --json ...

It runs the ordinary command line, so the scan is the same read-only scan as
a native install's. The file is kept to syntax any Python 3 can parse, so a
host Python older than 3.10 gets a plain message (exit status 64) rather
than a traceback.
"""
import os
import sys

TOO_OLD = 64


def main():
    if sys.version_info < (3, 10):
        sys.stderr.write("Doctor NoX needs Python 3.10 or later on this computer; /usr/bin/python3 is %d.%d\n"
                         % tuple(sys.version_info[:2]))
        return TOO_OLD
    # -I leaves the script's folder off sys.path: put the app's copy of the package first.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doctor_nox.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
