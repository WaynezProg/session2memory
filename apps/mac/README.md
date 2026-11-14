# session2memory Console (macOS)

A native macOS app (SwiftUI) for looking at what session2memory keeps and for testing
HKS queries. It has five panes:

- **Memory**: workspaces, their memory by status and kind, and for one entry its
  history, what it replaced or was replaced by, and the sessions it rests on.
  - Workspaces that have no memory yet are left out until **Without memory too** is
    ticked; a field filters them by name or path, and each shows the folder it lies in.
  - The search field looks through the chosen workspace or, with **All workspaces**,
    through every one (every word must be in the text; at most 500 are shown).
  - Entries can be retired, restored, reworded (**Edit…**, ⌘E) and written by hand
    (**New Memory**, ⇧⌘N). Several can be selected and retired or restored together,
    from the detail side, the context menu or the Delete key.
  - Every change asks for a reason, with a few ready-made ones, and is recorded. What a
    change came to stays on screen until dismissed, also when part of it failed.
  - Memory text is one line of at most 240 characters; the sheets count as you type.
- **Sessions**: the sessions of every agent, filtered by workspace, tool, scope, words in
  the title or summary, and whether they still wait for curation. Each shows its summary,
  the digest the model was shown, and the memory it is evidence for.
  - **Curate Again** sends a session back to curation and **Skip Curation** takes it out
    of the queue, for one session or several.
  - A session the run would pass over anyway says why (older than 21 days, too short, a
    scratch or deleted workspace, no workspace), and cannot be sent back.
- Memory and sessions link to each other: an evidence session opens in Sessions, and a
  memory listed under a session opens in Memory, with its workspace and row in view.
- **Runs**: store totals and every unattended run, step by step.
- **HKS Query**: runs `ks query … --writeback=no` and shows the answer, the route it came
  from, the evidence and the trace.
- **Settings**: what was found (the commands, the store), and overrides for unusual setups.

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
behind them were run against a copy of a real store and are covered by tests. The two
panes were drawn off screen against that copy (lists, one entry's detail, and arriving
from the other pane with the workspace and row scrolled into view). The sheets, context
menus and buttons have not been clicked through in the running app yet.

## Install and open

Requires macOS 13 or later and a Swift toolchain (Xcode 15 or later, or the command-line
tools).

```bash
apps/mac/scripts/make-app.sh --install    # builds "S2M Console.app" into /Applications
open -a "S2M Console"
```

Nothing has to be set up in the app. At start it looks for session2memory by itself:

1. the checkout the app was built from (it then runs `uv run session2memory` there);
2. otherwise the `session2memory` command on your `PATH`, as installed with
   `uv tool install git+https://github.com/WaynezProg/session2memory`.

HKS, which only the HKS Query pane needs, is looked for the same way: a
`Hybrid-Knowledge-System` checkout next to session2memory's, else the `ks` command.

If something is missing, the app says which one thing to do and gives the command to
copy: install uv, install session2memory, or run it once so there is a store to show.
**Check Again** looks anew. **Settings** shows what was found; its **Set by hand** part is
only for a command or folder in an unusual place.

For development, without making an app:

```bash
cd apps/mac
swift run            # builds and opens the window
swift test           # JSON decoding, shell quoting, finding the commands, the helpers
```

## How it starts the commands

Every command runs as `zsh -lc '<command>'` from the folder found above (the home folder
for an installed command), so it sees the same `PATH` as Terminal (uv, mise). Values are
passed single-quoted.

## What changes data

In the Memory pane: **Retire…**, **Restore…**, **Edit…** and **New Memory**. They call
`session2memory auto console retire|restore|edit|add` with the reason typed. Nothing is
deleted: an entry changes status, an edited entry stays as superseded with a link to its
new wording, and every change is written to the history.

In the Sessions pane: **Curate Again** and **Skip Curation** (`auto console
requeue|skip`). They only move a session into or out of the queue the next run reads;
the session's summary and the memory it already led to stay.

Everything else is read-only, and HKS queries always run with `--writeback=no`.
