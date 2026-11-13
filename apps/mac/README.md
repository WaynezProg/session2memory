# session2memory Console (macOS)

A native macOS app (SwiftUI) for looking at what session2memory keeps and for testing
HKS queries. It has five panes:

- **Memory**: workspaces, their memory by status, and for one entry its history, what it
  replaced or was replaced by, and the sessions it rests on. An active entry can be
  retired and a retired or stale one restored; both ask for a reason, which is recorded.
- **Sessions**: the sessions of every agent, filtered by workspace and tool, with the
  summary, the digest the model was shown, and the memory each session is evidence for.
- **Runs**: store totals and every unattended run, step by step.
- **HKS Query**: runs `ks query … --writeback=no` and shows the answer, the route it came
  from, the evidence and the trace.
- **Settings**: where the two repositories are and how their commands are started.

The app holds no SQL and does not open the database. It runs
`session2memory auto console <view>` and `ks query`, and renders the JSON they print, so
the schema can change without the app.

## Status

**This code has not been compiled.** It was written on a machine without a Swift toolchain
or macOS SDK. Expect to fix compile errors on the first build; the command-line side it
talks to (`auto console`) is covered by `tests/test_console.py`.

## Build and run

Requires macOS 13 or later and Xcode 15 or later (or the matching command-line tools).

```bash
cd apps/mac
swift run            # builds and opens the window
```

Or open `apps/mac/Package.swift` in Xcode and run the `S2MConsole` scheme.

On first start, open **Settings** in the sidebar, set the two repository folders, and
press **Check both commands**.

## How it starts the commands

Every command runs as `zsh -lc '<command>'` from the repository folder given in Settings,
so it sees the same `PATH` as Terminal (uv, mise). The defaults are `uv run session2memory`
and `uv run ks`. Values are passed single-quoted.

## What changes data

Only **Retire…** and **Restore…** in the Memory pane. They call
`session2memory auto console retire|restore --id … --reason …`. Nothing is deleted: the
entry changes status and the change is written to its history. Everything else is
read-only, and HKS queries always run with `--writeback=no`.
