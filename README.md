# session2memory

`session2memory` reads local coding-agent sessions and writes a refined folder that HKS can ingest.

Raw transcripts are never written into the HKS source folder. Generated output contains Markdown summaries plus evidence pointers back to the original local session files.

## P0 Command

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

## HKS Ingest

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```
