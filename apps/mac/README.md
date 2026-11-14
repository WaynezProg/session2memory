# session2memory Console (macOS)

A native macOS app (SwiftUI) for looking at what session2memory keeps and for testing
HKS queries. It has five panes:

- **Memory**: workspaces (with a filter field), their memory by status, and for one entry
  its history, what it replaced or was replaced by, and the sessions it rests on. The
  search field looks through the chosen workspace or, with **All workspaces**, through
  every one. Entries can be retired, restored, reworded (**Edit…**) and written by hand
  (**New Memory**); several can be selected and retired or restored together, from the
  detail side, the context menu or the Delete key. Every change asks for a reason, with a
  few ready-made ones, and is recorded.
- **Sessions**: the sessions of every agent, filtered by workspace, tool, words in the
  title or summary, and whether they still wait for curation. Each shows its summary, the
  digest the model was shown, and the memory it is evidence for. A session can be sent
  back to curation (**Curate Again**) or taken out of the queue (**Skip Curation**), one
  at a time or several together.
- Memory and sessions link to each other: an evidence session opens in Sessions, and a
  memory listed under a session opens in Memory.
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
store. The first version's five panes were opened against that store, and Retire and
Restore were pressed there.

Search, editing, adding, changing several entries at once, the session filters, Curate
Again / Skip Curation and the links between the two panes were added later. The commands
behind them were run against a copy of a real store and are covered by tests; the new
controls themselves have not been clicked through in the running app yet.

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

In the Memory pane: **Retire…**, **Restore…**, **Edit…** and **New Memory**. They call
`session2memory auto console retire|restore|edit|add` with the reason typed. Nothing is
deleted: an entry changes status, an edited entry stays as superseded with a link to its
new wording, and every change is written to the history.

In the Sessions pane: **Curate Again** and **Skip Curation** (`auto console
requeue|skip`). They only move a session into or out of the queue the next run reads;
the session's summary and the memory it already led to stay.

Everything else is read-only, and HKS queries always run with `--writeback=no`.
