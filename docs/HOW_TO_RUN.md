# IntelliFile, how to run it

IntelliFile searches the files on this computer by meaning, not just by name. Everything runs on this PC: no account, no internet, nothing leaves your laptop.

Requires Windows 10 or 11 (64-bit), about 3 GB of free disk space and 8 GB of RAM (16 GB recommended for Ask mode).

## 1. Launch

IntelliFile comes as one zip, `IntelliFile-windows.zip` (1.87 GB). It holds everything, including the local language model behind **Ask mode**.

1. Download `IntelliFile-windows.zip`, for example into `Downloads`.
2. Right-click it, choose **Extract All**, and set the destination to a folder you can write to, for example your Desktop. Extraction takes a few minutes. It creates an `IntelliFile` folder.
3. Open the `IntelliFile` folder and double-click `IntelliFile.exe`. Keep the other folders (`backend`, `models`, `data`, `sample-folder`) next to it.

Windows SmartScreen may say "Windows protected your PC", because the app is not signed by a registered publisher. Click **More info, then Run anyway**. It asks only once.

The first launch takes a few seconds (7 to 20 seconds on a typical laptop) while the search models load. The status at the bottom of the sidebar says **Starting…** and changes to **Ready** when IntelliFile can search. Later launches are faster.

Closing the main window quits IntelliFile completely, including its background engine. Starting it a second time brings the open window to the front.

## 2. Index a folder

Open **Index**, then click **Add folder**. To try it in under a minute, click **Try the sample folder**: it indexes `sample-folder`, which ships inside the `IntelliFile` folder next to `IntelliFile.exe`.

It holds 39 short documents (recipes, a gym plan, invoices, trip notes, engineering notes…) and three test images. Indexing it takes a few seconds. Your own folders work the same way. Documents, spreadsheets, slides, code, audio notes, photos and videos are all indexed.

## 3. Things to try

Type these in **Search** and press Enter:

| Try | What it shows |
|---|---|
| `gym plan` | a file named in the query is found instantly; the line under the search box says *Searched by filename, 3 ms* |
| `how do we add capacity when lots of visitors arrive` | no shared words with the file, found by meaning (*hybrid*) |
| `type:csv` | metadata filters alone list matching files |
| `in:invoices workshop` | filters narrow a text search |
| `? when is the march invoice due, and how much is it` | **Ask mode**: the local language model plans searches, reads the results and answers with citations. The closest passage appears at once; open "How it got there" to see each step. It takes 10–30 seconds depending on the CPU. |

Then:

- The **microphone button** in the search box: say a file name ("gym plan"). This is voice search, transcribed locally.
- **Photos and videos**: type `a red circle`.
- **Insights**: what the app has learned from your use: files you open most, topics, when you work, and how your searches were routed.
- **Settings**: switch "Personalize results" or "Remember my activity" off and on. "Clear activity" wipes the memory.
- **Ctrl+Space** anywhere on the desktop opens the quick-search overlay.

Open any result with **Enter**. Show it in File Explorer with **Ctrl+Enter**.

## 4. If something looks wrong

- **The sidebar says "Not running" for more than a minute:** close IntelliFile and open it again. If it stays offline, another program is using port 8756. Run `netstat -ano | findstr 8756` in a terminal to see which one.
- **Logs** (useful for a bug report) are in `%LOCALAPPDATA%\IntelliFile\logs\`: `backend.log`, `backend-console.log` and `shell.log`. Paste that path into the File Explorer address bar.
- **A folder indexed 0 files and shows a red note about access:** Windows refused to let IntelliFile read it, for example a folder owned by another user or protected by Controlled Folder Access. Allow it in **Windows Security, then Virus & threat protection, then Ransomware protection**, then use Re-index.
- **Voice search** needs microphone permission: **Settings, then Privacy & security, then Microphone, then Let desktop apps access your microphone**.
- **Resetting the app:** everything IntelliFile stores lives in `%LOCALAPPDATA%\IntelliFile`. Deleting that folder resets it. Your own files are never changed.
