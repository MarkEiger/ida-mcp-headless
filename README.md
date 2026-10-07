# ida-mcp-headless

Start, list and stop headless IDA Pro instances that serve the
[ida-pro-mcp](https://github.com/mrexodia/ida-pro-mcp) tools, from one command:

```
python3 ida_mcp_start.py BINARY_PATH MCP_PORT   # start
python3 ida_mcp_start.py --list                 # show running instances
python3 ida_mcp_start.py --stop PORT [PORT...]  # stop by port
python3 ida_mcp_start.py --stop all             # stop every headless instance
```

No IDA window opens. The instance runs detached in the background and serves
MCP at `http://127.0.0.1:MCP_PORT/mcp` once auto-analysis has finished.

## Why

The ida-pro-mcp GUI plugin only starts inside an IDA window and takes its port
from the database or a fixed default, so it cannot be launched for a given
binary and port from the command line. This script runs the headless (idalib)
server from the same package instead, which exposes the same tools, and turns
off its 10-minute idle shutdown so the instance stays up until you stop it.

## Requirements

- IDA Pro 9.0 or newer (idalib), with a licence that allows headless use
- `ida-pro-mcp` installed, e.g. `uv tool install ida-pro-mcp`
- Python 3.10+ to run the script itself
- macOS or Linux (tested on macOS with IDA 9.4 and ida-pro-mcp 2.0.0)

## Setup

```
cp ida_mcp_start.example.json ida_mcp_start.json
```

Then edit `ida_mcp_start.json`:

| Key | Meaning |
| --- | --- |
| `ida_path` | IDA install: the `.app` bundle, or the directory holding `libidalib` |
| `python` | Interpreter that has the `ida_pro_mcp` package installed |
| `host` | Address the MCP server binds to |
| `log_dir` | Where per-port logs go, relative to the script |
| `idle_ttl_sec` | Exit after this many idle seconds; `0` = never |
| `ready_timeout_sec` | How long a start waits for the port before returning anyway |
| `stop_timeout_sec` | How long a stop waits for IDA to save and exit |
| `unsafe` | Expose the tools ida-pro-mcp marks as unsafe |

## Behaviour

- **Lifetime.** A started instance survives the script, the terminal and
  whatever launched it. It ends when stopped or when the machine reboots.
- **Analysis first.** The port opens only after auto-analysis. A start waits
  up to `ready_timeout_sec`, then returns and leaves IDA working. An instance
  still analysing does not show in `--list` yet.
- **Stopping saves.** `--stop` sends SIGTERM and waits; IDA saves and packs
  the database into `BINARY.i64` next to the binary. It never escalates to
  SIGKILL. Do not `kill -9` an instance: that leaves an unpacked database.
- **GUI instances are safe.** `--list` also shows IDA windows running the MCP
  plugin (marked `gui`); `--stop` refuses to touch them.
- **Manual stop**, if needed: `kill $(lsof -ti tcp:PORT -sTCP:LISTEN)` or
  `pkill -f ida_pro_mcp.idalib_server`.

## Use from Python

```python
from pathlib import Path
from ida_mcp_start import start_instance, list_instances, stop_instances

inst = start_instance(Path("/tmp/target"), 13337)  # pid, url, log, ready
list_instances()                                   # live instances, by port
stop_instances([13337])                            # or stop_instances() for all
```

## Claude Code skill

`skill/ida-headless/SKILL.md` teaches Claude Code to launch IDA for itself
when a task needs reverse engineering. To install it:

1. Copy `skill/ida-headless` to `~/.claude/skills/`.
2. Replace `/path/to/ida-mcp-headless` in the copied `SKILL.md` with the real
   location of this repo.
3. Point an MCP server at the port you use, e.g.
   `claude mcp add --transport http ida-pro-mcp http://127.0.0.1:13337/mcp`.
4. Optionally allow the launcher without prompts in `~/.claude/settings.json`:

   ```json
   { "permissions": { "allow": ["Bash(python3 /path/to/ida-mcp-headless/ida_mcp_start.py *)"] } }
   ```
