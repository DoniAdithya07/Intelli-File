# IntelliFile UI: final implementation specification

Locked 27 September 2026. The source of truth for rebuilding the interface. Every displayed value, result, reason, metric, recommendation, question and status must come from the backend or from real application state. Items marked NEW still need implementation.

## 1. Window and navigation

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ IntelliFile                                                              _  □  X          │
├───────────────────┬──────────────────────────────────────────────────────────────────────┤
│                   │ ┌──────────────────────────────────────────────────────────────────┐ │
│ Search            │ │ Search files, or ask a question with ?                     mic   │ │
│ Photos            │ └──────────────────────────────────────────────────────────────────┘ │
│ Ask               │ All    Documents    Images    Audio                Filters    Sort   │
│ ─────────────     │ Searched by keywords + meaning, 12 ms                                │
│ For You           ├───────────────────────────────┬──────────────────────────────────────┤
│ Activity          │ RESULTS (ranked)              │ PREVIEW                              │
│ ─────────────     │ ┌───────────────────────────┐ │ Project_Report.pdf          PDF      │
│ Index             │ │ PDF  Project_Report.pdf   │ │ in Documents\Research                │
│ Settings          │ │ in Documents\Research     │ │                                      │
│ Help              │ │ Matched on meaning        │ │ Matching passage, page 4             │
│                   │ │ Strong match              │ │ "...additional server instances are  │
│                   │ └───────────────────────────┘ │  added when [traffic] demand rises..."│
│                   │ ┌───────────────────────────┐ │                                      │
│                   │ │ DOCX Research_Paper.docx  │ │ Next passage, page 5                 │
│                   │ │ Contains your words       │ │ "...the load balancer spreads..."    │
│                   │ └───────────────────────────┘ │ ──────────────────────────────────── │
│                   │ Weaker matches                │ WHY THIS FILE?                       │
│                   │ ┌───────────────────────────┐ │ Matched on meaning                   │
│                   │ │ PDF  Meeting_Notes.pdf    │ │ You use this often                   │
│ Works offline     │ └───────────────────────────┘ │ [ Open ]   [ Show in Explorer ]      │
│ Ctrl+Space        │                               │                                      │
│ quick search      │                               │                                      │
└───────────────────┴───────────────────────────────┴──────────────────────────────────────┘
```

- Sidebar: Search, Photos, Ask / For You, Activity / Index, Settings, Help / Works offline, Ctrl+Space quick search.
- Title bar: the native Windows title bar, showing only "IntelliFile".

## 2. Search

- **Input:** text, voice, or `?` to start Ask. Placeholder: "Search files, or ask a question with ?".
- **Tabs:** All, Documents, Images (screenshots and photos that contain text), Audio. No Videos tab: videos belong to Photos.
- **Filters (NEW):** Type, After, Before, Size, Folder. They generate `type:` `after:` `before:` `size:` `in:`.
- **Sort (NEW):** Relevance (default), Date modified, Name. Applied within the strong group and within the weak group, never across them.
- **Route evidence:** "Searched by <tier>, <ms>", the actual backend route.
- **Results:** one ranked list. Each result: file-type tag, file name, folder, backend reasons, strong or weak.
- **Weaker matches:** at most 3 shown.
- **Ask suggestion:** "This reads like a question. Ask instead" appears only when the backend reports `suggest_ask`, which means the query is a question **and** no confident result was found. *(Correction: not for every question.)*
- **Preview (NEW):** file name, type, folder; matching passage with the query words highlighted; page or slide number; next passage (from the NEW endpoint); WHY THIS FILE? with the backend's reasons only. No fake Word, PowerPoint or PDF rendering.
- **Actions:** Open, Show in Explorer.
- **Keyboard:** Up / Down move, Enter open, Ctrl+Enter show in Explorer, Esc clear.

## 3. Photos

- Separate visual-search experience. Input "Describe a photo", with a microphone.
- Tabs: All, Photos, Videos.
- Results: real indexed photos and video moments, each with its actual match strength.
- Preview: the image; for a video, the matched moment and its frame strip; the image's OCR text when it has any (NEW endpoint).
- Actions: Open, Show in Explorer.

## 4. Ask

- User-started (`?` or the Ask button); never runs automatically for normal searches.
- Each question is answered on its own; there is no follow-up memory. Questions from **this session** are listed newest first. *(Correction: the list is kept for the session; each question is still recorded in the activity history if Remember my activity is on.)*
- Each answer shows: the closest passage (at once), the answer, citations, Sources used (file, folder, page), Evidence used (passage, page), Search activity, Checks.
- Answer length: the model is asked for **1 to 2 sentences**; this is an instruction, not a hard limit. *(Correction.)*
- Fallback, exactly: "I couldn't find that in your files".
- **Figure warning:** shown when a number in the answer does not appear in the cited sources; the answer stays visible. *(Correction: it checks figures that are in the answer, not figures the answer "requires".)*

## 5. Ask: search activity

Observable tool actions only; never the model's reasoning.

```
Search activity
  First look   words: when is the march invoice due   route: keywords + meaning   19 ms
  Search       words: march invoice due date   mode: keywords   filters: none   route: keywords   8 ms
  Read more    march invoice.txt
  Answer written
```

- **mode** is what the agent asked for (Automatic, Meaning, Keywords, Exact phrase); **route** is the tier that actually ran. They can differ. *(Correction: the two were shown as the same value.)*

## 6. Ask: checks

Checks are made by the application's code, not the model. Two kinds of lines: *(Correction: the backend reports problems as they happen; passed checks are derived from the final answer.)*

- **Passed:** "Citations support the answer" (answer grounded), "All figures found in the cited sources" (no figure warning). "Filters respected" only when the agent used filters.
- **Problems:** citation removed (names the file), filters ignored (names them), premise not found (names the word or month), figure warning (names the figure), answer rejected (no source supports it).

## 7. Suggested questions

Only when they can be built from real recent files or activity. Never generic, invented questions. Otherwise, none are shown.

## 8. For You

- **Continue where you left off:** real recently opened files, with Open.
- **Frequently used:** real top files from activity.
- **Recommended:** likely next, usual at this time; each with its backend reason.
- **What IntelliFile learned:** activity by weekday and time (heatmap), topics learned from opened files, file-type shares.
- **Still learning:** "Still learning: n of 20", n counted from real activity.

## 9. Activity

- History grouped by day. Each entry: time, action, file or query.
- **Actions, all five kinds recorded by the backend:** Search, Opened, Shown in Explorer, Selected (a result clicked), Opened from recommendations. *(Correction: the spec listed three of the five.)*
- Controls: Remember my activity, Clear activity.

## 10. Index

- **Status:** Up to date, Indexing n of m, or Paused: reason.
- **Counts:** Files, Documents, Photos, Videos, Audio.
- **Scan:** Last scan (time the last scan finished), Scan now for all configured folders (NEW endpoint).
- **Indexed folders:** path, counts, Watching or Missing; Add folder, Add file, Re-index, Remove.
- **Failed or skipped files:** file name and reason, from the backend only.
- **Models on this computer:** Text, Photos, Speech, Reranker, OCR, Ask model; Installed or Not installed.

## 11. Search routing statistics

```
HOW YOUR SEARCHES WERE ROUTED
File name            share, average time
Metadata             share, average time
Keywords             share, average time
Keywords + meaning   share, average time   (includes searches that were reranked)

Reranking            n searches needed it
Escalations          n searches moved up a tier
Ask questions        n, counted separately, not a search route
```

- There are four search tiers. Reranking is not a fifth tier: it runs only when the keywords + meaning tier is still unsure after escalation (logged as the route `hybrid+rerank`).
- Computed from the last 500 remembered searches, so the statistics are empty while Remember my activity is off. *(Correction: the source and its limit were not stated.)*

## 12. Settings

- **Appearance:** Same as Windows, Day, Night.
- **File access:** Whole computer (your folders and drives, system folders skipped), Only folders I choose, Nothing.
- **Indexing:** Pause on battery, Pause in power-saving mode; Resource mode: Balanced, Performance, Battery saver.
- **Personalization:** Personalize results, on or off.
- **Privacy:** Remember my activity, Clear activity, Learn from files you opened in Windows (off by default); "IntelliFile never connects to the internet."
- **About:** version, index location, index size, Privacy policy, Terms of use, licence.

## 13. Help

- A sidebar page with the same content as `docs/USER_MANUAL.md`.
- Sections: Getting started, Searching, Reading the results, Photos and videos, Asking questions, For You and Activity, Index, Settings, Quick search, Keyboard shortcuts, Privacy, Troubleshooting.
- Every major screen has a small Help link to its section. Works offline. Screenshots use demo files only.

## 14. First run

```
Welcome to IntelliFile
Search and ask about your files. Nothing leaves this computer.

What may IntelliFile read?
  ( ) Whole computer
      Your folders and drives, system folders skipped.
      Indexing everything can take a long time, and Ask is slower until it finishes.
  ( ) Only folders I choose
  ( ) Nothing yet
      You can choose later in Settings.

[ Continue ]        [ Try the sample folder ]   (NEW)
```

*(Correction: the whole-computer note is added, because a fresh whole-computer index was measured to slow Ask past its time cap on this laptop.)*

## 15. Quick search (Ctrl+Space)

```
┌──────────────────────────────────────┐
│ Search files, or ask with ?          │
├──────────────────────────────────────┤
│ PDF   Project_Report.pdf             │
│       in Documents\Research          │
│ DOCX  Research_Paper.docx            │
│ XLSX  Data_Analysis.xlsx             │
└──────────────────────────────────────┘
Enter open, Ctrl+Enter show in Explorer, Esc close
```

Uses the same search backend as the main window.

## 16. User manual

- Ship `docs/USER_MANUAL.md`, and `HOW_TO_RUN.md`, in the download.
- Short, plain instructions, checked against the final application; never documents a control that does not exist.

## 17. Design rules

- **Layout:** native Windows title bar, sidebar, three-pane Search.
- **Type:** Segoe UI, Windows' own font.
- **Look:** Windows 11 File Explorer, Day and Night (section 28).
- **Icons:** plain line icons. **Files:** file-type tags.
- **Prohibited:** emoji, gradients, pill buttons, decorative motion, fake metrics, fake reasons, fake questions, fake document rendering.
- **Preview principle:** real indexed passages, real images, real OCR text.

## 18. Evidence mapping

- **Objective 1 (discovery and the agent):** search ranking, real reasons, preview evidence, Ask, sources, evidence, search activity, checks.
- **Objective 2 (personalization):** For You, frequently used, recommendations, Activity, learned topics, file-type shares, weekday and time heatmap, Personalize switch.
- **Objective 3 (routing):** search route and time, Index routing statistics, escalations, reranking count, models, index status.

## 19. Behaviour rules

Read-only. Offline. One ranked search result list. Ask is user-started, and each question is independent. Four search tiers; reranking is separate and only when needed. Photos and videos use the separate visual-search path. Every displayed value comes from real application state.

## 20. New backend work

| New part | Backend work |
|---|---|
| Preview and next passages | Endpoint returning the passages around a match |
| Image OCR text | Endpoint returning a file's OCR text |
| Scan now | Endpoint scanning all configured folders |
| First-run sample folder | Endpoint or configuration returning the sample folder path |

Everything else uses existing backend data. The reranking count, Ask count and activity grouping are derived in the interface from existing data.

## 21. Build order

1. Search  2. Ask  3. Photos  4. For You  5. Activity  6. Index  7. Settings  8. First run  9. Quick search  10. Help and user manual.

After Search, verify Day and Night, backend reasons, routing information, preview evidence, keyboard controls, strong and weak grouping, filters and sort before moving on.

## 22. Validation before final submission (separate from the UI work)

- **Required:** a clean Windows PC test of the packaged app.
- **Recommended:** a real photo folder, a normal usage period, an unplug test during indexing, and the full test suite with enough free memory (close Edge and Chrome before rebuilds and full runs).

## 23. States for every screen

No screen may show a blank area, a spinner with no words, or a raw error. Each state says what is happening and what to do.

| Screen | Empty | Loading | Error |
|---|---|---|---|
| Search | Before typing: recommendations and 3 example searches. No results: "Nothing on this computer matches ..." with the folder and file count | Result area keeps its size; "Searching..." under the box after 150 ms | "The search engine isn't running yet" with what to do; the typed query is kept |
| Preview | No result selected: "Select a result to see the matching passage" | Passage area shows "Loading passage" | "This file could not be read" with the reason (moved, deleted, locked) and Show in Explorer |
| Photos | No photos indexed: "Add a folder with photos in Index" | Grid placeholders the size of the thumbnails | A thumbnail that fails shows the file name and type |
| Ask | Before a question: what Ask can and cannot do, one line each. Ask model missing: "Ask needs its language model" with the steps | Closest passage appears first; "Writing the answer..." until tokens arrive | Model error: shown in words, the question kept for a retry |
| For You | Cold start: "Still learning: n of 20" and what counts as activity. Personalization off: "Personalization is off" with a link to Settings | Section placeholders | Section hidden, the rest still shown |
| Activity | "No activity yet" or "Remember my activity is off" | List placeholders | "Activity could not be loaded" with Retry |
| Index | No folders: "Add a folder to start" with Add folder and Try the sample folder | "Indexing n of m" with the current file | Failed files listed with reasons; a missing folder is marked Missing |
| Settings | Not applicable | Controls disabled until loaded | A setting that fails to save reverts and says so |
| Help | Not applicable | Not applicable | Not applicable (bundled text) |
| Quick search | Recent files | Same as Search | Same as Search |

## 24. Accessibility

- Every control reachable and usable with the keyboard; visible focus outline on every focusable element.
- Every icon-only button has a text label for screen readers; every input has a label.
- Text contrast at least 4.5:1 (normal) and 3:1 (large) in Day and in Night; checked on every screen.
- Nothing is conveyed by colour alone: strong and weak also differ in text ("Strong match", "Weaker matches").
- Works at 125% and 150% Windows display scaling without cut-off text.
- Respects "reduce motion".

## 25. Speed budgets (on the test laptop, 12th-gen i5, 16 GB)

| Action | Budget |
|---|---|
| App window visible after launch | under 2 s |
| Search ready after launch | under 15 s |
| Results on screen after Enter (typical search) | under 150 ms |
| Preview passage after selecting a result | under 200 ms |
| Tab or page switch | under 100 ms |
| Ask: closest passage on screen | under 1.5 s |
| Ask: complete answer | mean under 15 s, never over the 50 s cap |
| Photo results | under 1 s |

## 26. Acceptance tests

A screen is finished only when all its checks pass in both Day and Night.

| Screen | Pass when |
|---|---|
| Search | Ranked list matches the backend order; route line matches the backend route; reasons match the backend reasons exactly; at most 3 weaker matches; each tab and filter changes the query as specified; sort never moves a weak result above a strong one; preview shows the real passage with highlights and the correct page; Up/Down, Enter, Ctrl+Enter and Esc work |
| Photos | Results and match strengths match the backend; videos show the matched moment; OCR text shown only when the backend has it |
| Ask | Closest passage appears before the answer; citations and sources match the backend answer event; fallback text is exact; search activity lists exactly the tool calls made; no model reasoning text appears; checks match the backend |
| For You | Every file, reason, topic, share and heatmap cell matches the backend profile and recommendations; "Still learning" count is correct |
| Activity | Every entry matches a recorded event; Clear empties it; the switch stops recording |
| Index | Counts, status, folders, failed files and models match the backend; Scan now rescans every folder; routing shares add up to 100% and the reranking and Ask counts match the logged routes |
| Settings | Every setting survives a restart and changes behaviour as described |
| First run | Each access choice sets the matching backend mode; Try the sample folder indexes the bundled folder |
| Quick search | Same results as the main window for the same query |
| Help | Every instruction was followed on the built app and works |
| All | 0 network attempts with the offline guard on; no text contains emoji or em dashes; the full backend suite passes |

## 27. Scope and stages

The build is split so each stage ends with a working app.

| Stage | Contents | Ends with |
|---|---|---|
| A | Navigation shell, Search with preview, filters, sort, 3 of the 4 new endpoints | A working app with the new Search; Day and Night screenshots for review |
| B | Ask, Photos | Both screens passing their acceptance tests |
| C | For You, Activity, Index, Settings, First run | All pages; old pages removed |
| D | Quick search, Help, user manual | Manual checked against the app |
| E | Full backend suite, acceptance tests, speed budgets, accessibility check, rebuilt zips | Release candidate |

If time runs short, cut from the end of a stage, never mid-screen: Sort and suggested questions are the first to go.

## 28. Visual tokens

After Windows 11 File Explorer, chosen by the user on 27 September 2026 over the earlier "catalogue" design (screens in docs/screenshots/ui).

| Token | Day | Night |
|---|---|---|
| Window and navigation pane | #F3F3F3 | #202020 |
| Content panel (rounded top-left corner) | #F9F9F9 | #272727 |
| Cards and lists | #FFFFFF, 1px #E5E5E5 border | #2D2D2D, 1px #3A3A3A border |
| Text | #1A1A1A | #FFFFFF |
| Action colour and markers | Windows blue #005FB8 (text on it #FFFFFF) | #60CDFF (text on it #000000) |
| Selection | Explorer's light blue #DBEAFA | #2C3E4E |
| Current page | quiet fill and a short blue bar that slides to it | same |
| Personal reasons | amber #7A4600 on #F6EBDA | #FCE178 on #463C14 |
| File-type icons | PDF #B42318, Word #3056B5, PowerPoint #C2410C, Excel #1E7B45, images #0E7490, audio #9A5B00, text #525A66 | lighter versions of the same |
| Error | #B3261E | #FF99A4 |

Type: Windows' own Segoe UI (Cascadia Mono for paths, sizes and times), so no font files ship; sizes 26 / 15 / 14 / 13 / 12. Corners 4 (buttons, tags) and 8 (panels). Switches are Windows switches.

Motion (80 to 200 ms, only in answer to an action, all off when Windows' Animation effects are off): the navigation bar slides to the current page, switch knobs slide, the Filters popover and dialogs fade and grow in, the preview fades between results, pages fade in, buttons and rows change shade on hover and press, and Ask shows an indeterminate progress line while it searches. No scroll effects, hover lifts, staggered entrances, typing effects or animated logo.

Logo "Found": a page outline on a graphite tile with a blue marker dot (desktop/src/Logo.tsx, public/logo.svg, src-tauri/icons).
