#!/usr/bin/env python3
"""Run the bridge agent and observation agent together.

Launches the bridge (main.py) as the primary process and the observer
(observe_and_improve.py) alongside it in watch mode. When the observer
finds 5 improvements, it signals the bridge to save and quit.

Usage:
    python run.py --goal-file goals.txt --budget 50
    python run.py --multi-agent --goal-file goals.txt --budget 50 --observe-interval 5
    python run.py --goal "Clear Addamasartus"  # no observer, just bridge
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(
        description="Run bridge + observer together",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Bridge args (passed through to main.py)
    parser.add_argument("--port", type=int, default=21003)
    parser.add_argument("--model", type=str, default="claude-sonnet-4-20250514")
    parser.add_argument("--goal", type=str, default=None)
    parser.add_argument("--goal-file", type=str, default=None)
    parser.add_argument("--system-prompt", type=str, default=None)
    parser.add_argument("--time-limit", type=int, default=0)
    parser.add_argument("--pace", type=float, default=3.0)
    parser.add_argument("--no-director", action="store_true")
    parser.add_argument("--multi-agent", action="store_true")
    parser.add_argument("--director-interval", type=int, default=45)
    parser.add_argument("--budget", type=float, default=0.0)
    parser.add_argument("--verbose", action="store_true")

    # Observer args
    parser.add_argument("--no-observer", action="store_true",
        help="Disable the observation agent")
    parser.add_argument("--observe-interval", type=int, default=5,
        help="Minutes between observer analyses (default: 5)")
    parser.add_argument("--observe-output", type=str, default="live_report.md",
        help="File for observer reports (default: live_report.md)")

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(script_dir, ".venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = sys.executable

    # Build bridge command
    bridge_cmd = [venv_python, os.path.join(script_dir, "main.py")]
    bridge_cmd += ["--port", str(args.port)]
    bridge_cmd += ["--model", args.model]
    bridge_cmd += ["--pace", str(args.pace)]
    bridge_cmd += ["--director-interval", str(args.director_interval)]
    if args.goal:
        bridge_cmd += ["--goal", args.goal]
    if args.goal_file:
        bridge_cmd += ["--goal-file", args.goal_file]
    if args.system_prompt:
        bridge_cmd += ["--system-prompt", args.system_prompt]
    if args.time_limit:
        bridge_cmd += ["--time-limit", str(args.time_limit)]
    if args.budget:
        bridge_cmd += ["--budget", str(args.budget)]
    if args.no_director:
        bridge_cmd.append("--no-director")
    if args.multi_agent:
        bridge_cmd.append("--multi-agent")
    if args.verbose:
        bridge_cmd.append("--verbose")

    # Clean up stale shutdown file
    shutdown_file = os.path.join(script_dir, ".shutdown")
    if os.path.exists(shutdown_file):
        os.remove(shutdown_file)

    print("=" * 60)
    print("OpenMW Bridge — Agent + Observer")
    print("=" * 60)
    print(f"Bridge: {' '.join(bridge_cmd[-6:])}")
    if not args.no_observer:
        print(f"Observer: every {args.observe_interval}m -> {args.observe_output}")
    print()

    # Start bridge
    bridge_proc = subprocess.Popen(
        bridge_cmd,
        cwd=script_dir,
        env=os.environ.copy(),
    )

    observer_proc = None
    if not args.no_observer:
        # Give bridge a head start
        time.sleep(10)

        # Start observer
        observer_cmd = [
            venv_python, os.path.join(script_dir, "observe_and_improve.py"),
            "--watch",
            "--interval", str(args.observe_interval),
            "--output", args.observe_output,
        ]
        observer_proc = subprocess.Popen(
            observer_cmd,
            cwd=script_dir,
            env=os.environ.copy(),
        )
        print(f"Observer started (PID {observer_proc.pid})")

    try:
        # Wait for bridge to finish (it's the primary process)
        bridge_proc.wait()
        print(f"\nBridge exited with code {bridge_proc.returncode}")
    except KeyboardInterrupt:
        print("\nCtrl+C — shutting down...")
        bridge_proc.send_signal(signal.SIGINT)
        try:
            bridge_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            bridge_proc.kill()
    finally:
        if observer_proc and observer_proc.poll() is None:
            observer_proc.terminate()
            try:
                observer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                observer_proc.kill()
            print("Observer stopped.")

    print("Done.")


if __name__ == "__main__":
    main()
