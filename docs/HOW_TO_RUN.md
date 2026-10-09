# IntelliFile, how to run it

IntelliFile searches the files on this computer by meaning, not just by name. Everything runs on this PC: no account, no internet, nothing leaves your laptop.

Requires Windows 10 or 11 (64-bit), about 3 GB of free disk space and 8 GB of RAM (16 GB recommended for Ask mode).

## 1. Launch

IntelliFile comes as one zip, `IntelliFile-windows.zip` (1.87 GB). It holds everything, including the local language model behind **Ask mode**.

1. Download `IntelliFile-windows.zip`, for example into `Downloads`.
2. Right-click it, choose **Extract All**, and set the destination to a folder you can write to, for example your Desktop. Extraction takes a few minutes. It creates an `IntelliFile` folder.
3. Open the `IntelliFile` folder and double-click `IntelliFile.exe`. Keep the other folders (`backend`, `models`, `data`, `sample-folder`) next to it.

Windows SmartScreen may say "Windows protected your PC", because the app is not signed by a registered publisher. Click **More info, then Run anyway**. It asks only once.

The first launch takes 25 to 34 seconds (measured 27.8, 29.4, 33.9 s) while the search engine loads its models. The sidebar shows **Starting…** then **Works offline** when the engine is ready. Later launches are faster (about 6 to 15 seconds).

Closing the main window quits IntelliFile completely, including its background engine. Starting it a second time brings the open window to the front.

## 2. Index a folder

Open **Index**, then click **Add folder** (hold **Ctrl** to pick several folders at once). To try it in under a minute, click **Try the sample folder**: it indexes `sample-folder`, which ships inside the `IntelliFile` folder next to `IntelliFile.exe`.

It holds 39 short documents (recipes, a gym plan, invoices, trip notes, engineering notes…) and three test images. Indexing it takes a few seconds. Your own folders work the same way. Documents, spreadsheets, slides, code, audio notes, photos and videos are all indexed.

**Optional:** On the **For You** page, if the page says **Still learning**, click **Load sample history** to fill it with a made-up four weeks of use of the sample folder. This makes recommendations visible on a fresh install. You can remove this history in **Settings** > **Activity** or on the **For You** page.

## 3. Things to try

Type these in **Search** and press Enter:

| Try | What it shows |
|---|---|
| `gym plan` | a file named in the query is found at once; the line under the box says *Searched by file name* |
| `how do we add capacity when lots of visitors arrive` | no shared words with the file: found by meaning (*keywords + meaning*); the preview on the right shows the matching passage and *Why this file?* |
| `type:csv` | filter words alone list matching files (also under **Filters**) |
| `in:invoices workshop` | filters narrow a text search |
| `? when is the march invoice due, and how much is it` | **Ask**: the closest passage appears at once, then an answer that names its sources, with **Search activity** and **Checks** below it. It takes 10 to 30 seconds. |

Then:

- The **microphone button** in the search box: say a file name ("gym plan"). This is voice search, transcribed on this computer.
- **Photos**: type `a red circle`.
- **For You** and **Activity**: what IntelliFile has learned from your use, and everything it remembered.
- **Index**: counts, folders, **Scan now**, and how your searches were routed.
- **Settings**: Day or Night, personalization, privacy and indexing switches, and the privacy policy and terms.
- **Help**: the full user manual, also in `docs/USER_MANUAL.md`.
- **Ctrl+Space** anywhere in Windows opens quick search.

Open a result with **Enter** (pressed again on the same words) or **Open**. Show it in File Explorer with **Ctrl+Enter**.

## 4. If something looks wrong

- **The sidebar says "Search engine stopped":** close IntelliFile and open it again. If it stays offline, another program is using port 8756. Run `netstat -ano | findstr 8756` in a terminal to see which one.
- **Logs** (useful for a bug report) are in `%LOCALAPPDATA%\IntelliFile\logs\`: `backend.log`, `backend-console.log` and `shell.log`. Paste that path into the File Explorer address bar.
- **A folder indexed 0 files and shows a red note about access:** Windows refused to let IntelliFile read it, for example a folder owned by another user or protected by Controlled Folder Access. Allow it in **Windows Security, then Virus & threat protection, then Ransomware protection**, then use Re-index.
- **Voice search** needs microphone permission: **Settings, then Privacy & security, then Microphone, then Let desktop apps access your microphone**.
- **Resetting the app:** everything IntelliFile stores lives in `%LOCALAPPDATA%\IntelliFile`. Deleting that folder resets it. Your own files are never changed.
