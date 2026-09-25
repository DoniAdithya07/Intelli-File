# IntelliFile — how to run it

IntelliFile searches the files on this computer by meaning, not just by name. Everything runs on this machine: no account, no internet, nothing leaves your laptop.

## 1. Launch

**Windows:** run `IntelliFile_x.y.z_x64-setup.exe` (or unzip the portable folder and double-click `IntelliFile.exe`). SmartScreen may say "Windows protected your PC" because the app is not signed by a registered publisher — click **More info → Run anyway**.

**macOS:** open the `.dmg` and drag IntelliFile to Applications (or run the `.app` from anywhere). The app is not signed with an Apple developer certificate, so macOS will object once:

- macOS 14 and earlier: **right-click the app → Open → Open**.
- macOS 15 (Sequoia) and later: double-click, dismiss the warning, then **System Settings → Privacy & Security → scroll down → "Open Anyway"**, and confirm.
- If macOS says the app is *"damaged"* instead: open Terminal and run `xattr -dr com.apple.quarantine /Applications/IntelliFile.app`, then open it again.

The first launch takes about 30–60 seconds while the local AI models load — the badge bottom-left says **"Starting the engine…"** and turns into "Engine Online" when ready. Later launches are faster.

macOS will also ask, the first time IntelliFile reads a folder like Desktop, Documents or Downloads, whether to allow it. Say yes; if you said no, allow it later under **System Settings → Privacy & Security → Files and Folders → IntelliFile**, then use Re-index.

## 2. Index a folder

Click **Folders → Add folder**. To try it in under a minute, add the bundled sample folder:

- Windows: `<install folder>\resources\sample-folder`
- macOS: right-click IntelliFile.app → Show Package Contents → `Contents/Resources/sample-folder`

It holds 39 short documents (recipes, a gym plan, invoices, trip notes, engineering notes…) and three test images. Indexing it takes a few seconds. Your own folders work the same way — documents, spreadsheets, slides, code, audio notes, photos and videos are all indexed.

## 3. Things to try

Type these in **Search** (press Enter):

| Try | What it shows |
|---|---|
| `gym plan` | a file named in the query is found instantly — the route badge says *filename · 3 ms* |
| `how do we add capacity when lots of visitors arrive` | no shared words with the file — found by meaning (*hybrid*) |
| `type:csv` | metadata filters alone list matching files |
| `in:invoices workshop` | filters narrow a text search |
| `? when is the march invoice due, and how much is it` | **Ask mode**: the local AI plans searches, reads the results and answers with citations — watch "How it got there" |

Then:

- **🎤** in the search box: say a file name ("gym plan") — voice search, transcribed locally.
- **Photos & Videos**: type `a red circle`.
- **Insights**: what the app has learned from your use — files you open most, topics, when you work, and how your searches were routed.
- **Settings**: switch "Personalize results" or "Remember my activity" off and on; "Clear activity" wipes the memory.
- **Ctrl+Space** anywhere on the desktop opens the quick-search overlay.

Open any result with **Enter**, reveal it in Explorer/Finder with **Ctrl/⌘+Enter**.

## 4. If something looks wrong

- "Engine Offline" for more than a minute: quit IntelliFile fully and open it again; on Windows check that port 8756 is free.
- Logs, if you want to send them: `~/Library/Application Support/IntelliFile/logs/` (macOS) or `%LOCALAPPDATA%\IntelliFile\logs\` (Windows) — `backend.log`, `backend-console.log` and `shell.log`.
- A folder that indexed 0 files and shows a red note about access: the operating system refused to let IntelliFile read it — allow it in the system's privacy settings, then Re-index.
- Voice search needs microphone permission the first time.
- Everything the app stores lives in `%LOCALAPPDATA%\IntelliFile` (Windows) or `~/Library/Application Support/IntelliFile` (macOS); deleting that folder resets the app.
