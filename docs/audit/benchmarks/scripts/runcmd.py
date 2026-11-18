"""Run a command, report wall time, CPU time (user+sys) and peak RSS of the child."""

import json
import resource
import subprocess
import sys
import time

t = time.perf_counter()
p = subprocess.run(sys.argv[1:], capture_output=True, text=True)
w = time.perf_counter() - t
r = resource.getrusage(resource.RUSAGE_CHILDREN)
print(
    json.dumps(
        {
            "wall_s": round(w, 2),
            "cpu_s": round(r.ru_utime + r.ru_stime, 2),
            "peak_rss_mb": round(r.ru_maxrss / 1024, 1),
            "rc": p.returncode,
            "err": p.stderr[-300:],
        }
    )
)
