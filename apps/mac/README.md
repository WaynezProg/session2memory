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

It builds with Swift 6.4 (Xcode 27) on macOS 27 and `swift test` passes. The models in
`Models.swift` were checked against what `auto console` and `ks query` print for a real
store. All five panes were opened against that store: Memory with one entry's detail,
Sessions with a digest, Runs with a report, HKS Query with a hit and a no-hit, and
Settings with **Check both commands**. Retire and Restore have not been pressed.

## Build and run

Requires macOS 13 or later and Xcode 15 or later (or the matching command-line tools).

```bash
cd apps/mac
swift run            # builds and opens the window
```

Or open `apps/mac/Package.swift` in Xcode and run the `S2MConsole` scheme.

No folder is built in. On first start the session2memory folder is the checkout the app
was built in or started from (the nearest folder above it that holds `src/session2memory`),
and the HKS folder is a checkout named `Hybrid-Knowledge-System` next to it, if there is
one. Anything not found is left empty: open **Settings** in the sidebar, set the two
repository folders, and press **Check both commands**. What you set there is kept.

```bash
swift test           # JSON decoding, shell quoting, finding the checkout
```

## How it starts the commands

Every command runs as `zsh -lc '<command>'` from the repository folder given in Settings,
so it sees the same `PATH` as Terminal (uv, mise). The defaults are `uv run session2memory`
and `uv run ks`. Values are passed single-quoted.

## What changes data

Only **Retire…** and **Restore…** in the Memory pane. They call
`session2memory auto console retire|restore --id … --reason …`. Nothing is deleted: the
entry changes status and the change is written to its history. Everything else is
read-only, and HKS queries always run with `--writeback=no`.
