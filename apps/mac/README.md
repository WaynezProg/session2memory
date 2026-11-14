# session2memory Console (macOS)

A native macOS app (SwiftUI) for looking at what session2memory keeps and for testing
HKS queries. Its interface is in Traditional Chinese and says things in plain words; ids,
paths and raw figures sit in a closed "給工程用" group on each page. It has six pages:

- **總覽** (overview): how many memories are in use, how many sessions have been dealt
  with and how many still wait, the sessions each recent run read, and the workspaces
  with the most memory.
- **記憶** (memory): one workspace's memory, picked from a menu, with a chip per kind and
  a status menu. The right side shows one entry: its text, the sessions it rests on and
  its history.
  - The search field looks through the chosen workspace or, with **搜尋所有專案**,
    through every one (every word must be in the text; at most 500 are shown).
  - Entries can be retired (**停用**), restored (**復原**), reworded (**改寫**, ⌘E) and
    written by hand (**新增記憶**, ⇧⌘N). Several can be selected and retired or restored
    together, from the detail side, the context menu or the Delete key.
  - Every change asks for a reason, with a few ready-made ones, and is recorded. What a
    change came to stays on screen until dismissed, also when part of it failed.
  - Memory text is one line of at most 240 characters; the sheets count as you type.
- **對話** (sessions): the sessions of every agent, filtered by workspace, tool, scope,
  words in the title or summary, and whether they still wait for curation. Each shows its
  summary and the memory it is evidence for; the digest the model was shown is under
  "給工程用".
  - **再整理一次** sends a session back to curation and **略過，不整理** takes it out of
    the queue, for one session or several. A session sent back by hand is read by the
    next run whatever its age; left alone, a run drops sessions that have waited more
    than 21 days, which the session then says, with **仍要整理** to keep it.
  - A session no run would read says why (too short to distil, a scratch or deleted
    workspace, no workspace) and cannot be sent back.
- Memory and sessions link to each other: an evidence session opens in 對話, and a
  memory listed under a session opens in 記憶, with its workspace and row in view.
- **整理紀錄** (runs): store totals and every unattended run by day, each told in a
  sentence per step, with the full report under "給工程用".
- **問知識庫** (HKS query): runs `ks query … --writeback=no` and shows the answer, how
  sure it is, where it came from and which ways of looking found something.
- **設定** (settings): what was found (the commands, the store), and overrides for
  unusual setups.

Times are shown in the Mac's time zone. The app is drawn in light colours whatever the
system appearance is.

The app holds no SQL and does not open the database. It runs
`session2memory auto console <view>` and `ks query`, and renders the JSON they print, so
the schema can change without the app.

## Status

It builds with Swift 6.4 (Xcode 27) on macOS 27 and `swift test` passes. After the
redesign every page was opened against a copy of a real store and an HKS query was run.
In the installed app, against that copy, these were done by hand: add, reword, retire and
restore one entry, retire and restore several at once, 再整理一次 and 略過，不整理 on a
session, the context menu of both lists (restore several; curate again), and ⌘E, ⇧⌘N and
Delete. Not clicked: 停用… and 改寫… in the memory list's context menu (they call what the
buttons call), 複製文字, and the first-run screens.

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
**重新檢查** looks anew. **設定** shows what was found; its **手動指定** part is
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

In 記憶: **停用**, **復原**, **改寫** and **新增記憶**. They call
`session2memory auto console retire|restore|edit|add` with the reason typed. Nothing is
deleted: an entry changes status, an edited entry stays as superseded with a link to its
new wording, and every change is written to the history.

In 對話: **再整理一次** and **略過，不整理** (`auto console requeue|skip`). They only move a session into or out of the queue the next run reads,
and note that a person asked for it; the session's summary and the memory it already led
to stay.

Everything else is read-only, and HKS queries always run with `--writeback=no`.
