import os
import sys
import time

# Test helper for test_compaction.py: writes its own PID to a marker file,
# then sleeps indefinitely. Used to verify run_bounded_process kills the
# whole process group (this child must not survive the parent's timeout).
marker = sys.argv[1]
with open(marker, "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
time.sleep(60)
