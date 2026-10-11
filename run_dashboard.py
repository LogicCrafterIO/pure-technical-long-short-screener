"""
One-command launcher for the dashboards.

There are two front ends:

    app.py        the pipeline dashboard — runs/refreshes the screen and shows it
    dashboard.py  the CSV explorer — filtering, sorting and charts over the
                  saved screen CSV (no pipeline, nothing is downloaded)

Usage:

    python run_dashboard.py                       # CSV explorer (default)
    python run_dashboard.py --mode pipeline       # pipeline dashboard
    python run_dashboard.py --mode pipeline --run # build/refresh, then serve
    python run_dashboard.py --port 8600           # pick a port
    python run_dashboard.py --mode pipeline --run --force

With --run it builds the screen in a normal terminal first (so you get real
progress output), then starts Streamlit.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APPS = {
    "csv": os.path.join(HERE, "dashboard.py"),
    "pipeline": os.path.join(HERE, "app.py"),
}


def free_port(preferred: int) -> int:
    for p in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("no free port found")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=sorted(APPS), default="csv",
                    help="which dashboard to serve (default: csv explorer)")
    ap.add_argument("--run", action="store_true",
                    help="run the screen before launching (implies --mode pipeline)")
    ap.add_argument("--force", action="store_true",
                    help="force universe and price re-download")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--labels", nargs="*", default=None,
                    help="subset of universes, e.g. SP500 RUSSELL2000")
    args = ap.parse_args()

    mode = "pipeline" if args.run else args.mode
    app = APPS[mode]

    if args.run:
        sys.path.insert(0, HERE)
        from screener import run_screen
        print("Running screen (this can take a few minutes on a cold cache)...")
        run_screen(labels=args.labels,
                   force_universe=args.force,
                   force_prices=args.force,
                   verbose=True)

    if mode == "csv" and not os.path.exists(
            os.path.join(HERE, "output", "latest_screen.csv")):
        print("No output/latest_screen.csv yet — run "
              "`python run_dashboard.py --run` first, or upload a CSV in the app.")

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  {mode} dashboard: {url}\n")
    cmd = [sys.executable, "-m", "streamlit", "run", app,
           "--server.port", str(port),
           "--server.address", "127.0.0.1",
           "--server.headless", "true",
           "--browser.gatherUsageStats", "false"]
    return subprocess.call(cmd, cwd=HERE)


if __name__ == "__main__":
    raise SystemExit(main())
