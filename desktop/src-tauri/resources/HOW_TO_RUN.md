# IntelliFile — how to run it

IntelliFile searches the files on this computer by meaning, not just by name. Everything runs on this PC: no account, no internet, nothing leaves your laptop.

Requires Windows 10 or 11 (64-bit), about 3 GB of free disk space and 8 GB of RAM (16 GB recommended for Ask mode).

## 1. Launch

Unzip `IntelliFile-windows.zip` anywhere you can write to, for example your Desktop or `Documents`. Then double-click `IntelliFile.exe` in the unzipped folder. Keep the `resources` folder next to it.

Windows SmartScreen may say "Windows protected your PC", because the app is not signed by a registered publisher. Click **More info → Run anyway**. It asks only once.

The first launch takes about 30–60 seconds while the local AI models load. The badge in the bottom-left says **"Starting the engine…"** and turns into **"Engine Online"** when it is ready. Later launches are faster.

Closing the main window quits IntelliFile completely, including its background engine. Starting it a second time brings the open window to the front.

## 2. Index a folder

Click **Folders → Add folder**. To try it in under a minute, add the bundled sample folder: `resources\sample-folder`, next to `IntelliFile.exe`.

It holds 39 short documents (recipes, a gym plan, invoices, trip notes, engineering notes…) and three test images. Indexing it takes a few seconds. Your own folders work the same way. Documents, spreadsheets, slides, code, audio notes, photos and videos are all indexed.

## 3. Things to try

Type these in **Search** and press Enter:

| Try | What it shows |
|---|---|
| `gym plan` | a file named in the query is found instantly; the route badge says *filename · 3 ms* |
| `how do we add capacity when lots of visitors arrive` | no shared words with the file, found by meaning (*hybrid*) |
| `type:csv` | metadata filters alone list matching files |
| `in:invoices workshop` | filters narrow a text search |
| `? when is the march invoice due, and how much is it` | **Ask mode**: the local AI plans searches, reads the results and answers with citations (watch "How it got there"). It takes 10–30 seconds depending on the CPU. |

Then:

- **🎤** in the search box: say a file name ("gym plan"). This is voice search, transcribed locally.
- **Photos & Videos**: type `a red circle`.
- **Insights**: what the app has learned from your use: files you open most, topics, when you work, and how your searches were routed.
- **Settings**: switch "Personalize results" or "Remember my activity" off and on. "Clear activity" wipes the memory.
- **Ctrl+Space** anywhere on the desktop opens the quick-search overlay.

Open any result with **Enter**. Show it in File Explorer with **Ctrl+Enter**.

## 4. If something looks wrong

- **"Engine Offline" for more than a minute:** close IntelliFile and open it again. If it stays offline, another program is using port 8756. Run `netstat -ano | findstr 8756` in a terminal to see which one.
- **Logs** (useful for a bug report) are in `%LOCALAPPDATA%\IntelliFile\logs\`: `backend.log`, `backend-console.log` and `shell.log`. Paste that path into the File Explorer address bar.
- **A folder indexed 0 files and shows a red note about access:** Windows refused to let IntelliFile read it, for example a folder owned by another user or protected by Controlled Folder Access. Allow it in **Windows Security → Virus & threat protection → Ransomware protection**, then use Re-index.
- **Voice search** needs microphone permission: **Settings → Privacy & security → Microphone → Let desktop apps access your microphone**.
- **Resetting the app:** everything IntelliFile stores lives in `%LOCALAPPDATA%\IntelliFile`. Deleting that folder resets it. Your own files are never changed.
