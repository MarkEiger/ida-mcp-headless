---
name: ida-headless
description: Reverse engineering and binary analysis with IDA Pro. Load this FIRST for any task that involves analysing a compiled binary, even if the user never mentions IDA - reverse engineering, RE, disassembling, decompiling, finding or understanding a function, xrefs, strings, patching, vulnerability research, malware or firmware analysis, CTF binaries, Mach-O/ELF/PE/dylib/kext/.so/.dll/.exe files, .i64/.idb databases. It lets you launch headless IDA with the ida-pro-mcp tools yourself, so do this instead of falling back to otool, objdump, nm, strings, radare2 or Ghidra, and instead of asking the user to open IDA. Also use it when the ida-pro-mcp MCP server is not connected or failed to connect, has the wrong binary open, or when the user asks to open, switch or close a binary in IDA.
---

# Headless IDA with MCP

A launcher script runs IDA headless (idalib, no window) with the ida-pro-mcp
server on a port you choose. Run it yourself; do not ask the user to open IDA.

```
python3 /path/to/ida-mcp-headless/ida_mcp_start.py BINARY_PATH PORT
python3 /path/to/ida-mcp-headless/ida_mcp_start.py --list
python3 /path/to/ida-mcp-headless/ida_mcp_start.py --stop PORT [PORT...]
python3 /path/to/ida-mcp-headless/ida_mcp_start.py --stop all
```

Run these as plain commands with that exact prefix (no `cd`, no pipes) so the
permission allow rule matches.

## Workflow

1. `--list` first. If the binary is already open, reuse that instance.
2. Start on port **13337**: the `ida-pro-mcp` MCP server in Claude Code is
   configured as `http://127.0.0.1:13337/mcp`, so only that port shows up as
   `mcp__ida-pro-mcp__*` tools. If 13337 is taken by another binary the user
   still needs, use another port and the curl fallback below; do not stop an
   instance you did not start without asking.
3. The start command returns once the server is listening, or after 120 s if
   auto-analysis is still running (it says "Still analysing"). In that case
   wait and poll the port or the log; the instance is not in `--list` until
   analysis finishes.
4. If the `mcp__ida-pro-mcp__*` tools are missing after a start on 13337, the
   session connected to MCP before IDA was up. Ask the user to run `/mcp` and
   reconnect `ida-pro-mcp`, or use the curl fallback meanwhile.
5. When the work is done, stop the instances you started with `--stop PORT`.
   They otherwise keep running after the session ends.

## Curl fallback (any port, no reconnect needed)

```
curl -s -X POST http://127.0.0.1:PORT/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Call a tool with `"method":"tools/call","params":{"name":"<tool>","arguments":{...}}`.

## Things to know

- IDA writes its database (`BINARY.i64`) next to the binary. For a binary in
  a read-only or system location, copy it somewhere writable first.
- Never `kill -9` an instance: plain `--stop` (SIGTERM) lets IDA save the
  database; a hard kill leaves it unpacked and needing repair.
- `--stop` only touches headless instances. Entries marked `gui` in `--list`
  are IDA windows the user opened; leave them alone.
- Tools that ida-pro-mcp marks unsafe (e.g. running Python in IDA, debugger)
  are off. Enabling them means setting `"unsafe": true` in
  `/path/to/ida-mcp-headless/ida_mcp_start.json`; ask the user first.
- Logs: `/path/to/ida-mcp-headless/logs/ida_mcp_<PORT>.log`.
- Full details are in the script's docstring (`--help`).
