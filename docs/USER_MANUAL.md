# IntelliFile user manual

IntelliFile finds files on your computer by what they say, what they show and what is spoken in them, and answers questions from them. Everything runs on this computer: there is no account, no internet connection and nothing is uploaded. IntelliFile only reads your files; it never changes, moves or deletes them.

## Getting started

1. Extract `IntelliFile-windows.zip` (right-click, **Extract All**) to a folder you can write to, for example your Desktop.
2. Open the `IntelliFile` folder and double-click `IntelliFile.exe`. If Windows says "Windows protected your PC", click **More info**, then **Run anyway**. Keep the other folders next to `IntelliFile.exe`.
3. The first time, IntelliFile asks **What may IntelliFile read?**
   - **Whole computer:** your folders and drives. Windows, program files, AppData and installed software such as Python environments and toolchains are skipped automatically. The first scan of a whole computer can still take an hour or more (about an hour for 13,000 files), and Ask is slower until it finishes. For the fastest start choose **Only folders I choose** and pick Documents, Desktop, Downloads and Pictures. Searching works while the scan runs, and later scans read only what changed.
   - **Only folders I choose:** you add folders or single files on the **Index** page. Nothing else is read.
   - **Nothing yet:** you can choose later in **Settings**.

   Click **Continue**, or click **Try the sample folder** to index a small demo folder (39 documents and 3 pictures) in a few seconds.
4. The bottom of the left list says **Works offline** and how many files are indexed. While the search models load it says **Starting**; this takes a few seconds.

"The left list works like Windows Explorer: **Home**, **Photos**, your pinned folders, and **This PC** with its drives. Below them, under **IntelliFile**, are **Search**, **Ask**, **For You**, **Activity**, **Index**, **Settings** and **Help**. The moon or sun button at the bottom switches between Night and Day.

## What IntelliFile can read

| Kind | Types | Found by |
|---|---|---|
| Documents | PDF, Word (.docx, .doc), text, Markdown, RTF, OpenDocument text (.odt), e-books (.epub), e-mails (.eml, .msg) | the words inside |
| Spreadsheets | Excel (.xlsx, .xlsm, .xls), .csv, .tsv, OpenDocument (.ods) | the cell contents |
| Presentations | PowerPoint (.pptx, .ppt), OpenDocument (.odp) | the text on the slides |
| Web pages and code | .html, and about 30 code and settings types (.py, .js, .ts, .java, .c, .cpp, .cs, .go, .rs, .json, .xml, .yaml, .sql, .sh and more) | the text inside |
| Photos | .jpg, .png, .gif, .bmp, .webp, .avif, .heic (iPhone), .tif | what is in the picture, and any words in it |
| Videos | .mp4, .mov, .m4v, .mkv, .webm, .avi, .3gp, .wmv, .mts, .mpg | what is in the frames; the result shows the matching moment |
| Audio | .mp3, .wav, .m4a, .flac, .ogg, .aiff, .wma, .aac, .opus; also voice notes saved as .mpeg (WhatsApp) and other video-type files that have only sound | the spoken words (English), written down on your computer, and the file name and folder name |

Limits: PDF, Word and PowerPoint files over 50 MB are skipped, and so are text, spreadsheet and code files over 5 MB. Only the first 500 pages of a PDF are read, and OCR reads up to 100 scanned pages. To keep the first scan fast, only the beginning of a long file is searchable by meaning: about the first 8 passages (roughly 3 pages) of code, JSON, XML, CSV and other data files, 40 (roughly 17 pages) of text, Markdown and web pages, and 150 (roughly 60 pages) of PDF, Word and other documents; file names are always searchable. Old Word, Excel and PowerPoint files (.doc, .xls, .ppt) are read as well as possible; a password-protected or damaged file is listed under **Files that could not be read** with the reason. Files that are only stored online in OneDrive are skipped until you make them available offline. Skipped files are listed under **Files skipped** on the **Index** page.

## Browsing

- **Home** shows **Quick access** (your Windows folders, folders you pinned and folders you indexed) and a list of **Recent**, **Favorites** and **Frequently used** files, with when you last used them.
- **This PC** shows each drive with how full it is and how many files IntelliFile has indexed on it.
- Click a folder to open it. The list shows **Name**, **Date modified**, **Type** and **Size**; click a heading to sort. **Searchable: Yes** marks files IntelliFile has indexed. Use **Sort**, **View** (**Details**, **List**, **Large icons**) and **Filter** on the command bar. Right-click a file or folder for **Open**, **Show in Explorer**, **Copy path**, **Add to Favorites**, **Pin to the left list** and indexing choices. **Index folder** adds the folder you are in to what IntelliFile can search.
- The back, forward and up arrows and the path at the top work as in Explorer: click a part of the path to go there, or click the empty part to type a path.
- IntelliFile opens only files it has indexed (documents, pictures, audio and video). For any other file it offers **Show in Explorer**. It never changes, moves or deletes a file, so there is no Cut, Paste, Rename or Delete.

## Searching

Type in the search box at the top right and press **Enter**. Enter only searches; it never opens a file. In a folder, the box has a switch: **This folder** (typing filters the names at once; Enter searches the contents of that folder and its subfolders) or **Everywhere**. Describe what the file is about in your own words, quote a phrase you remember, or type part of its name. Misspelled words and file names are corrected; when IntelliFile changes a word it offers **Did you mean**.

- **Voice:** click the microphone, speak, and click it again to stop. The words appear in the box; press **Enter** to search. If nothing was heard, IntelliFile says so.
- **Kinds:** **All**, **Documents**, **Images** (screenshots and pictures that contain text) and **Audio**.
- **Filter:** on the command bar, limit by type, **Modified after**, **Modified before**, size or a word from the folder's name. Each filter shows as a chip you can remove.
- **Sort:** **Relevance** (the default), **Name**, **Date modified** or **Size**. Sorting never moves a possible match above a best match.
- **Search words:** you can also type filters yourself.

| Type | To find |
|---|---|
| `type:pdf` | one kind of file; also `type:document`, `type:image`, `type:audio`, `type:video`, `type:email` |
| `after:2026-03-01` | files changed on or after a date (`after:2026` and `after:2026-03` work too) |
| `before:2026-04-01` | files changed before a date |
| `size:>10mb` | files larger than a size (`size:<500kb` for smaller) |
| `in:invoices` | files whose folder path contains the word |
| `"exact words"` | the whole phrase, in this order |
| `? your question` | a question, answered on the **Ask** page |

Before you search, **Search** shows your **Recent searches** and a few files **For You**.

## Reading the results

The status bar says how many results were found and how long it took. **More > How this was searched** adds a line such as **Searched by keywords + meaning, 28 ms**. IntelliFile picks the quickest way that can answer: by file name, by file details (the filters), by keywords, or by keywords and meaning together. When it is unsure, it tries the next way and, if needed, reranks the results.

Results come as a table: name, the passage that matched, why it matched (**Words in the file**, **Same meaning**, **File name**, **Text in the picture** or **Spoken words**), folder, date and size. Reasons in amber come from your own habits, such as **You use this often**. Files that match well are under **Best matches**; up to five loosely related files follow under **Possible matches**.

The **preview** on the right (**Alt+P** shows or hides it) shows the **Matching passage** with your words highlighted, its page or slide, the **Next passage**, and **Why this file?** Click **Open** to open the file in its usual program, or **Show in Explorer** to see it in its folder.

When a question finds nothing confident, IntelliFile offers **Ask instead**.

On the **Audio** tab the file name and the name of its folder count too, so a song is found by its title even though nothing in it can be read. Speech search understands English only; for a recording in another language, type part of its file name (IntelliFile says so when the Audio tab finds nothing). Music and silence give little or no text: Whisper's invented filler and repeated phrases are thrown away.

## Photos and videos

Open **Photos** and describe the picture, for example `a red circle` or `a person in a red shirt`. A short phrase works better than a single word. Type in the search box and press **Enter**.

- **Kinds:** **All**, **Photos**, **Videos**.
- Results are grouped as **Best matches** and **Possible matches**. Only a possible match carries a label on its picture. Pictures are matched by a model on this computer, so some results can be wrong.
- For a video, the time of the matching moment is shown on the picture, and the best moments are in the preview.
- Double-click or press **Enter** to open a result, or right-click for **Show in Explorer**.

Words that appear inside screenshots and scanned pictures are read on this computer by Windows' own text recognition, so the **Search** page finds them too. A picture needs at least five readable words for its text to be indexed.

## Asking questions

Open **Ask**, type a question and press **Enter** (**Shift+Enter** starts a new line), or click the microphone. Suggested questions appear when IntelliFile has learned your topics and recent files. You can also start a question from **Search** by typing `?` first.

- The **closest passage** from your files appears within a few seconds, while the answer is written.
- The answer names its sources with numbers. **Sources** lists each file with **Open** and **Show**; **Show evidence** shows the passages it used.
- **Search activity** lists every search IntelliFile made to answer, and **Checks** shows what IntelliFile's own code verified: that the citations support the answer and that every number in it appears in the sources. A problem is listed there, and a number that is not in the sources is shown as a warning.
- When the answer is not in your files, IntelliFile says **I couldn't find that in your files** instead of guessing.
- Each question is answered on its own; questions from this session are listed on the right. **New question** starts again.

Some questions are about your files themselves, not about what is written in them. IntelliFile answers these at once, exactly, from its index, and says **Answered from the index**:

- **Counting:** `how many files are there`, `how many movies are in the laptop`, `how many png files do i have`, `how many pdfs are in the invoices folder`, `how many videos and photos do i have`. Spelling mistakes are understood.
- **Listing:** `list my videos`, `which spreadsheets do i have`.
- **By time and place:** `how many images did i download in the last hour`, `what did i download today`, `how many files were changed this week`, `how many photos are on the desktop`, `newest files in downloads`. "Downloaded" means your Downloads folder; "added" is the time the file appeared on this computer and "changed" the time it was last modified. Only indexed files are counted, and a file saved in the last minute may not be indexed yet.
- **By name:** for a word that is not a kind of file, such as `how many quizzes are there`, IntelliFile counts the files and folders with that word in their name and says so. To look inside files, use **Search**.
- **Problems:** `how many files could not be read`, `how many files were skipped`.
- **A missing file:** `why is budget.xlsx missing`, `why can't i find report.pdf`, `where is my resume.docx`. IntelliFile says what it knows: the file is indexed and where it is, it could not be read and why, it was skipped and why, it was deleted, moved or renamed while IntelliFile was open, its type is one IntelliFile does not read, indexing is still running, or no file with that name is in the folders IntelliFile searches.

A question such as `how many files did the backup copy` is about what a file says. Its answer comes from that file and carries a note that the number is not a count of your own files.

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
- **Indexing:** **Pause on battery** (off by default), **Pause in power-saving mode** (on by default), and the **Resource mode**: **Balanced**, **Performance** or **Battery saver**. Indexing that pauses resumes from the same file. **Search what is said in videos** (off by default): also writes down the first 10 minutes of each video's soundtrack, after everything else has been indexed, so a spoken sentence finds the video. Switching it on reads the sound of videos already indexed (their pictures are not redone); switching it off stops it for new videos, and what was already read stays searchable until the video is re-indexed.
- **Local models**, **Network** (IntelliFile never connects to the internet) and **About** (version, where the index is kept, its size, the licence, the **Privacy policy** and the **Terms of use**).

## Quick search

Press **Ctrl+Space** anywhere in Windows to open a small search window over whatever you are doing. It searches the same way as the main window. Type and press **Enter** to search, move with **Up** and **Down**, press **Enter** again to open the selected file, **Ctrl+Enter** to show it in File Explorer, and **Esc** to close. Before you type, it lists your recent files. Start with `?` to ask a question.

## Keyboard shortcuts

| Keys | What they do |
|---|---|
| Ctrl+Space | open quick search from anywhere |
| Enter in the search box | search (it never opens a file) |
| Up / Down / Left / Right | move through a list, grid or the left tree |
| Home / End, PageUp / PageDown | first or last item, one screen |
| Enter in a list | open the selected folder or file (indexed files only) |
| Ctrl+Enter | show the selected item in File Explorer |
| Ctrl+Shift+C | copy the path of the selected items |
| Shift+F10 or the Menu key | context menu |
| Ctrl+A, Ctrl+Click, Shift+Click | select all, add to or extend the selection |
| Letters | jump to the first name that starts with them |
| Backspace or Alt+Up | go up one folder |
| Alt+Left / Alt+Right | back / forward |
| Alt+Home | go to Home |
| Ctrl+L, Alt+D or F4 | type a folder path |
| Ctrl+F, Ctrl+E or F3 | go to the search box |
| Down in the search box | move into the results without opening anything |
| F5 or Ctrl+R | refresh the folder or search again |
| Alt+P | show or hide the preview pane |
| Ctrl+Shift+6 / Ctrl+Shift+2 | Details / Large icons |
| Esc | close a menu, clear the search, clear the selection; in quick search, close it |
| Shift+Enter | a new line in an Ask question |

## Privacy

- IntelliFile works offline. The search, photo, speech and language models all run on this computer.
- It only reads your files. It never changes, moves, renames or deletes them.
- The index and your activity are kept in `%LOCALAPPDATA%\IntelliFile` on this computer. Deleting that folder resets IntelliFile completely.
- Activity is used only to rank results and suggest files. It can be switched off and cleared in **Settings** and **Activity**.
- The full **Privacy policy** and **Terms of use** are in **Settings**, under **About**.

## Troubleshooting

- **IntelliFile says "Search engine stopped":** close IntelliFile and open it again. If it keeps stopping, another program may be using port 8756; run `netstat -ano | findstr 8756` in a terminal to see which one.
- **A folder indexed nothing:** Windows may be blocking IntelliFile, for example for a folder of another user or one protected by Controlled Folder Access. Allow it in **Windows Security**, then **Virus & threat protection**, then **Ransomware protection**, then click **Re-index**.
- **Voice search hears nothing:** allow microphone access in Windows **Settings**, then **Privacy & security**, then **Microphone**, then **Let desktop apps access your microphone**.
- **Ask needs its language model:** extract `IntelliFile-windows.zip` again, completely, so the `models\llm` folder is next to the others.
- **A file is missing from results:** ask `why is <file name> missing` in **Ask**; a search for a file name that finds nothing also says why. Or check **Index** for **Files that could not be read** and **Files skipped**, and that its folder is listed. A folder on a drive that is not plugged in stays listed, marked as missing, until the drive is back or you remove it. A picture's text is indexed only when it has at least five readable words.
- **Logs** for a bug report are in `%LOCALAPPDATA%\IntelliFile\logs`: `backend.log`, `backend-console.log` and `shell.log`.
