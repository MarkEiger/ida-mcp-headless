#!/usr/bin/env python3
"""Start, list and stop headless IDA (idalib) instances serving ida-pro-mcp.

Usage:
  python3 ida_mcp_start.py BINARY_PATH MCP_PORT   start an instance
  python3 ida_mcp_start.py --list                 show running instances
  python3 ida_mcp_start.py --stop PORT [PORT...]  stop the instances on these ports
  python3 ida_mcp_start.py --stop all             stop every headless instance

Lifetime
  A started instance is detached from this script and from the terminal. It
  keeps running after the script returns, after the shell closes and after
  whatever launched it (e.g. Claude) stops. It ends only when it is stopped,
  when the machine reboots, or after `idle_ttl_sec` idle seconds if that is
  set to something other than 0.

Stopping
  Use --stop, or do the same by hand:
    kill $(lsof -ti tcp:PORT -sTCP:LISTEN)    one instance, by port
    pkill -f ida_pro_mcp.idalib_server        all headless instances
  Always send a plain kill (SIGTERM): IDA then saves and packs the database
  into BINARY.i64. With `kill -9` it dies immediately and leaves unpacked
  .id0/.id1/.nam/.til files next to the binary that IDA will want to repair.

Where things are
  MCP endpoint   http://HOST:PORT/mcp
  Log            <log_dir>/ida_mcp_<PORT>.log
  Database       BINARY.i64, written next to the binary
  Registry       ~/.idapro/mcp/instances/instance_<PORT>.json (pid, binary);
                 written by ida-pro-mcp itself, and what --list reads. GUI IDA
                 windows running the MCP plugin show up there too; --stop
                 never touches those.

Config (ida_mcp_start.json next to this script)
  ida_path           IDA install (the .app bundle or the dir holding libidalib)
  python             interpreter that has the ida_pro_mcp package installed
  host               address the MCP server binds to
  log_dir            where per-port logs go (relative to this script)
  idle_ttl_sec       exit after this many idle seconds, 0 = never
  ready_timeout_sec  how long a start waits for the port before returning anyway
  stop_timeout_sec   how long a stop waits for IDA to save and exit
  unsafe             expose the tools ida-pro-mcp marks as unsafe
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "ida_mcp_start.json"
INSTANCES_DIR = Path.home() / ".idapro" / "mcp" / "instances"
WORKER_MARKER = "ida_pro_mcp.idalib_server"

# Runs inside the worker interpreter. The idle TTL has no CLI flag, so it is
# set on the class before idalib_server builds its watchdog at import time.
BOOTSTRAP = """
import sys
from ida_pro_mcp import worker_lifecycle
worker_lifecycle.WorkerLifecycle.IDLE_TTL_SEC = float(sys.argv.pop(1))
from ida_pro_mcp.idalib_server import main
main()
"""


class LauncherError(Exception):
    pass


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        raise LauncherError(f"Config not found: {CONFIG_PATH}")
    except json.JSONDecodeError as e:
        raise LauncherError(f"Bad JSON in {CONFIG_PATH}: {e}")


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_headless_worker(pid: int) -> bool:
    """True if `pid` is really an idalib MCP worker (guards against pid reuse)."""
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True
    ).stdout
    return WORKER_MARKER in out


def start_instance(binary: Path, port: int, cfg: dict | None = None) -> dict:
    """Launch a detached headless IDA on `binary` serving MCP on `port`.

    Returns {"pid", "host", "port", "url", "log", "ready"}. `ready` is False
    if auto-analysis was still running when ready_timeout_sec elapsed; the
    instance keeps going and starts listening once analysis is done.
    """
    cfg = cfg if cfg is not None else load_config()
    ida_path = Path(cfg["ida_path"]).expanduser()
    python = Path(cfg["python"]).expanduser()
    host = cfg.get("host", "127.0.0.1")
    binary = binary.expanduser().resolve()

    if not binary.is_file():
        raise LauncherError(f"Binary not found: {binary}")
    if not ida_path.exists():
        raise LauncherError(f"ida_path does not exist: {ida_path}")
    # IDADIR must be the directory holding the IDA binaries, not the bundle.
    if (ida_path / "Contents" / "MacOS").is_dir():
        ida_path = ida_path / "Contents" / "MacOS"
    if not python.exists():
        raise LauncherError(f"python does not exist: {python}")
    if not 1 <= port <= 65535:
        raise LauncherError(f"Invalid port: {port}")
    if port_open(host, port):
        raise LauncherError(f"Port {port} is already in use on {host}")

    log_dir = (SCRIPT_DIR / cfg.get("log_dir", "logs")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"ida_mcp_{port}.log"

    cmd = [
        str(python), "-c", BOOTSTRAP, str(cfg.get("idle_ttl_sec", 0)),
        "--host", host, "--port", str(port),
    ]
    if cfg.get("unsafe"):
        cmd.append("--unsafe")
    cmd.append(str(binary))

    env = dict(os.environ, IDADIR=str(ida_path))
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,  # survive this script and the terminal
        )

    # The server only starts listening once auto-analysis has finished.
    deadline = time.monotonic() + float(cfg.get("ready_timeout_sec", 120))
    ready = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = "".join(log_path.read_text(errors="replace").splitlines(True)[-20:])
            raise LauncherError(
                f"IDA exited with code {proc.returncode}. Log tail:\n{tail}"
            )
        if port_open(host, port):
            ready = True
            break
        time.sleep(0.5)

    return {
        "pid": proc.pid,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}/mcp",
        "log": str(log_path),
        "ready": ready,
    }


def list_instances() -> list[dict]:
    """Return the live MCP instances from ida-pro-mcp's registry, by port.

    Each entry has host, port, pid, binary, idb_path, started_at and backend
    ("worker" = headless, "gui" = an IDA window). Entries whose process is
    gone are skipped. An instance still in auto-analysis is not listed yet,
    because it registers itself only once the database is open.
    """
    instances = []
    for path in INSTANCES_DIR.glob("instance_*.json"):
        try:
            info = json.loads(path.read_text())
            pid = int(info["pid"])
            int(info["port"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid_alive(pid):
            instances.append(info)
    return sorted(instances, key=lambda i: int(i["port"]))


def stop_instances(ports: list[int] | None = None, cfg: dict | None = None) -> dict[int, str]:
    """Stop the headless instances on `ports`, or all of them if `ports` is None.

    Sends SIGTERM so IDA saves the database, then waits up to stop_timeout_sec
    for each to exit; it never escalates to SIGKILL. Returns {port: status}
    with status "stopped", "still running", "not found" or "not headless".
    """
    cfg = cfg if cfg is not None else load_config()
    timeout = float(cfg.get("stop_timeout_sec", 60))
    by_port = {int(i["port"]): i for i in list_instances()}
    if ports is None:
        ports = [p for p, i in by_port.items() if i.get("backend") == "worker"]

    results: dict[int, str] = {}
    pending: dict[int, int] = {}
    for port in ports:
        info = by_port.get(port)
        if info is None:
            results[port] = "not found"
        elif info.get("backend") != "worker" or not is_headless_worker(int(info["pid"])):
            results[port] = "not headless"
        else:
            pid = int(info["pid"])
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                results[port] = "stopped"
                continue
            pending[port] = pid

    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for port, pid in list(pending.items()):
            if not pid_alive(pid):
                results[port] = "stopped"
                del pending[port]
        if pending:
            time.sleep(0.25)
    for port in pending:
        results[port] = "still running"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("binary", type=Path, nargs="?", help="binary (or .i64) to open")
    parser.add_argument("port", type=int, nargs="?", help="port for the MCP server")
    parser.add_argument("--list", action="store_true", help="show running instances")
    parser.add_argument(
        "--stop", nargs="+", metavar="PORT", help="ports to stop, or 'all'"
    )
    args = parser.parse_args()

    try:
        if args.list:
            instances = list_instances()
            if not instances:
                print("No running instances.")
            for i in instances:
                print(
                    f"{i['port']:<6} pid {i['pid']:<7} {i.get('backend', '?'):<7}"
                    f" {i.get('idb_path') or i.get('binary', '')}"
                )
        elif args.stop:
            if args.stop == ["all"]:
                ports = None
            else:
                try:
                    ports = [int(p) for p in args.stop]
                except ValueError:
                    parser.error("--stop takes port numbers, or 'all'")
            results = stop_instances(ports)
            if not results:
                print("No headless instances to stop.")
            for port, status in results.items():
                print(f"{port}: {status}")
            if any(s != "stopped" for s in results.values()):
                sys.exit(1)
        elif args.binary is not None and args.port is not None:
            print(f"Starting IDA on {args.binary.name}, waiting for analysis...")
            inst = start_instance(args.binary, args.port)
            if inst["ready"]:
                print(f"MCP server ready: {inst['url']}")
            else:
                print(f"Still analysing; it will listen on {inst['url']} when done.")
            print(f"Log:  {inst['log']}")
            print(f"Stop: python3 {Path(__file__).name} --stop {inst['port']}  (pid {inst['pid']})")
        else:
            parser.error("give BINARY_PATH and MCP_PORT, or --list, or --stop")
    except LauncherError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
