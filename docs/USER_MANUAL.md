# IntelliFile user manual

IntelliFile finds files on your computer by what they say, what they show and what is spoken in them, and answers questions from them. Everything runs on this computer: there is no account, no internet connection and nothing is uploaded. IntelliFile only reads your files; it never changes, moves or deletes them.

## Getting started

1. Extract `IntelliFile-windows.zip` (right-click, **Extract All**) to a folder you can write to, for example your Desktop.
2. Open the `IntelliFile` folder and double-click `IntelliFile.exe`. If Windows says "Windows protected your PC", click **More info**, then **Run anyway**. Keep the other folders next to `IntelliFile.exe`.
3. The first time, IntelliFile asks **What may IntelliFile read?**
   - **Whole computer:** your folders and drives; system folders are skipped. Indexing everything can take a long time, and Ask is slower until it finishes.
   - **Only folders I choose:** you add folders or single files on the **Index** page. Nothing else is read.
   - **Nothing yet:** you can choose later in **Settings**.

   Click **Continue**, or click **Try the sample folder** to index a small demo folder (39 documents and 3 pictures) in a few seconds.
4. The bottom of the sidebar says **Works offline** and how many files are indexed. While the search models load it says **Starting**; this takes a few seconds.

The sidebar has three groups: **Search**, **Photos** and **Ask** to find things; **For You** and **Activity** for what IntelliFile has learned; **Index**, **Settings** and **Help** to run it. The moon or sun button at the bottom switches between Night and Day.

## Searching

Type in the box at the top of **Search** and press **Enter**. Describe what the file is about in your own words, quote a phrase you remember, or type part of its name. Misspelled words and file names are corrected; when IntelliFile changes a word it offers **Did you mean**.

- **Voice:** click the microphone, speak, and click it again to stop. The words appear in the box; press **Enter** to search. If nothing was heard, IntelliFile says so.
- **Tabs:** **All**, **Documents**, **Images** (screenshots and pictures that contain text) and **Audio**.
- **Filters:** click **Filters** to limit by type, **Modified after**, **Modified before**, size or a word from the folder's name. The filter words appear under the tabs after **Filtered by**.
- **Sort:** **Relevance** (the default), **Date modified** or **Name**. Sorting never moves a weaker match above a strong one.
- **Search words:** you can also type filters yourself.

| Type | To find |
|---|---|
| `type:pdf` | one kind of file; also `type:document`, `type:image`, `type:audio`, `type:video` |
| `after:2026-03-01` | files changed on or after a date (`after:2026` and `after:2026-03` work too) |
| `before:2026-04-01` | files changed before a date |
| `size:>10mb` | files larger than a size (`size:<500kb` for smaller) |
| `in:invoices` | files whose folder path contains the word |
| `"exact words"` | the whole phrase, in this order |
| `? your question` | a question, answered on the **Ask** page |

Before you search, the page shows your **Recent searches** and a few files **For You**.

## Reading the results

The line under the box says how IntelliFile searched and how long it took, for example **Searched by keywords + meaning, 28 ms**. IntelliFile picks the quickest way that can answer: by file name, by file details (the filters), by keywords, or by keywords and meaning together. When it is unsure, it tries the next way and, if needed, reranks the results.

Results come as one ranked list. Each shows the file's type, name and folder, the passage that matched, and why it matched, for example **Matched based on meaning similarity** or **Contains: invoice**. Reasons in amber come from your own habits, such as **You use this often**. Files that match well say **Strong match**; up to three loosely related files follow under **Weaker matches**.

The **preview** on the right shows the **Matching passage** with your words highlighted, its page or slide, the **Next passage**, and **Why this file?** Click **Open** to open the file in its usual program, or **Show in Explorer** to see it in its folder.

When a question finds nothing confident, IntelliFile offers **Ask instead**.

## Photos and videos

Open **Photos** and describe the picture, for example `a red circle` or `a person in a red shirt`. A short phrase works better than a single word. Before you search, the page shows your photos and videos, newest first.

- **Tabs:** **All**, **Photos**, **Videos**.
- Each result says **Strong match** or **Weaker match**.
- For a video, the time of the matching moment is shown on the picture, and a strip of the best moments below it.
- Hover over a result for **Open** and **Show in Explorer**, double-click to open it, or right-click to show it in File Explorer.

Words that appear inside screenshots and scanned pictures are read on this computer by Windows' own text recognition, so the **Search** page finds them too. A picture needs at least five readable words for its text to be indexed.

## Asking questions

Open **Ask**, type a question and press **Enter** (**Shift+Enter** starts a new line), or click the microphone. Suggested questions appear when IntelliFile has learned your topics and recent files. You can also start a question from **Search** by typing `?` first.

- The **closest passage** from your files appears within a few seconds, while the answer is written.
- The answer names its sources with numbers. **Sources** lists each file with **Open** and **Show**; **Show evidence** shows the passages it used.
- **Search activity** lists every search IntelliFile made to answer, and **Checks** shows what IntelliFile's own code verified: that the citations support the answer and that every number in it appears in the sources. A problem is listed there, and a number that is not in the sources is shown as a warning.
- When the answer is not in your files, IntelliFile says **I couldn't find that in your files** instead of guessing.
- Each question is answered on its own; questions from this session are listed on the right. **New question** starts again.

Answers usually take 10 to 30 seconds and never more than 50. If Ask says it needs its language model, the `models\llm` folder is missing: extract the zip again, completely.

## For You and Activity

**For You** shows what IntelliFile has learned from how you use your files: **Continue where you left off**, the files you use most (**Frequently used**), **Recommended** files for now with the reason for each, and **What IntelliFile learned**: your topics, the file types you use, and when you work. Until it has seen 20 actions it says **Still learning**. If you are testing with the sample folder, you can click **Load sample history** to fill the page with a made-up four weeks of use; you can remove this history in **Settings** > **Activity** or on this page. Personalization only reorders results that are nearly tied, and every result says why.

**Activity** lists what you did, grouped by day: searches, opened files, files shown in File Explorer, results you selected and files opened from recommendations. Choose which kind to show at the top. **Clear activity** deletes it all (IntelliFile asks first). Switch **Remember my activity** off to stop recording.

## Index

**Index** shows what IntelliFile has read:

- the status: **Up to date**, **Indexing n of m**, or **Paused** with the reason;
- how many files, documents, photos, videos and audio files are indexed;
- **Last scan** and **Scan now**, which checks every folder again (unchanged files are skipped quickly);
- **Indexed folders**, each with **Re-index** and **Remove**. Removing a folder removes it from the index only; your files are not touched;
- **Add folder** (hold **Ctrl** to select several folders at once), **Add file** (several files at once too) and **Try the sample folder**;
- **Files that could not be read**, with the reason;
- **How your searches were routed**, from your last 500 searches;
- **Models on this computer**.

Changes in your folders are picked up within about 30 seconds while IntelliFile is running, and at every start.

## Settings

- **Appearance:** **Same as Windows**, **Day** or **Night**.
- **Search:** **Personalize results**.
- **File access:** **Whole computer**, **Only folders I choose** or **Nothing**. **Manage indexed folders** opens **Index**.
- **Privacy:** **Remember my activity**; **Learn from files you opened in Windows** (off by default: reads Windows' list of recently opened files so For You can start at once); **Open Activity** and **Clear activity** (IntelliFile asks first).
- **Indexing:** **Pause on battery** (off by default), **Pause in power-saving mode** (on by default), and the **Resource mode**: **Balanced**, **Performance** or **Battery saver**. Indexing that pauses resumes from the same file.
- **Local models**, **Network** (IntelliFile never connects to the internet) and **About** (version, where the index is kept, its size, the licence, the **Privacy policy** and the **Terms of use**).

## Quick search

Press **Ctrl+Space** anywhere in Windows to open a small search window over whatever you are doing. It searches the same way as the main window. Type and press **Enter** to search, move with **Up** and **Down**, press **Enter** again to open the selected file, **Ctrl+Enter** to show it in File Explorer, and **Esc** to close. Before you type, it lists your recent files. Start with `?` to ask a question.

## Keyboard shortcuts

| Keys | What they do |
|---|---|
| Ctrl+Space | open quick search from anywhere |
| Enter | search; pressed again on the same words, open the selected result |
| Up / Down | move through the results |
| Ctrl+Enter | show the selected result in File Explorer |
| Esc | clear the search; in quick search, close it |
| Shift+Enter | a new line in an Ask question |

## Privacy

- IntelliFile works offline. The search, photo, speech and language models all run on this computer.
- It only reads your files. It never changes, moves, renames or deletes them.
- The index and your activity are kept in `%LOCALAPPDATA%\IntelliFile` on this computer. Deleting that folder resets IntelliFile completely.
- Activity is used only to rank results and suggest files. It can be switched off and cleared in **Settings** and **Activity**.
- The full **Privacy policy** and **Terms of use** are in **Settings**, under **About**.

## Troubleshooting

- **The sidebar says "Search engine stopped":** close IntelliFile and open it again. If it keeps stopping, another program may be using port 8756; run `netstat -ano | findstr 8756` in a terminal to see which one.
- **A folder indexed nothing:** Windows may be blocking IntelliFile, for example for a folder of another user or one protected by Controlled Folder Access. Allow it in **Windows Security**, then **Virus & threat protection**, then **Ransomware protection**, then click **Re-index**.
- **Voice search hears nothing:** allow microphone access in Windows **Settings**, then **Privacy & security**, then **Microphone**, then **Let desktop apps access your microphone**.
- **Ask needs its language model:** extract `IntelliFile-windows.zip` again, completely, so the `models\llm` folder is next to the others.
- **A file is missing from results:** check **Index** for **Files that could not be read**, and that its folder is listed. A picture's text is indexed only when it has at least five readable words.
- **Logs** for a bug report are in `%LOCALAPPDATA%\IntelliFile\logs`: `backend.log`, `backend-console.log` and `shell.log`.
