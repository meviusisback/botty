"""Test helper for test_compaction.py.

Deliberately NOT named test_*.py: unittest discovery imports every file matching
that pattern, and importing this module used to write the process pid into a file
named after sys.argv[1] (which is the word "discover" during discovery) - a stray
6-byte file in the repository folder.
"""
import os
import sys
import time


def main() -> None:
    # Writes its own PID to a marker file, then sleeps: used to verify that
    # run_bounded_process kills the whole process group (this child must not
    # survive the parent's timeout).
    marker = sys.argv[1]
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    time.sleep(60)


if __name__ == "__main__":
    main()
