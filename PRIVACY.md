# Privacy policy

Last updated 26 September 2026. The same text is shown in the app under Settings, About IntelliFile, Privacy policy.

## The short version

IntelliFile runs entirely on your computer. It has no account, makes no internet connections, and sends nothing anywhere: no telemetry, no analytics, no crash reports, no update checks.

## What IntelliFile reads

Only the folders and files you allow on the first-run screen or in Folders. You can change this at any time in Settings, under File access.

It reads files to index them. It never changes, moves, renames or deletes your files. It opens a file only when you ask it to.

If you switch on *Learn from files you opened in Windows*, it also reads the Recent items list Windows keeps for your account. Only entries for files in your indexed folders are used. Switching the option off removes everything it imported.

## What IntelliFile stores, and where

Everything is kept in `%LOCALAPPDATA%\IntelliFile` on this computer: the search index (text passages, their search vectors, and a list of your indexed files with paths, sizes, dates and content fingerprints), your settings, and log files.

If *Remember my activity* is on, it also keeps a history of your searches and of the files you open or show in File Explorer from IntelliFile. This history personalizes your results. Switch it off in Settings to stop recording; *Clear activity* deletes it.

Photo thumbnails are made when shown and are not saved.

## Voice search

The microphone is used only between your click to start recording and your click to stop. The audio is turned into text on this computer, in memory, and is not saved. Only the resulting search text can be kept, as part of your activity history.

## The local connection

The app window talks to its search engine through a connection that only this computer can reach (127.0.0.1), protected by a key that changes every time IntelliFile starts.

## Removing your data

Remove a folder in Folders to delete its part of the index. *Clear activity* in Settings deletes your history. To remove everything, quit IntelliFile and delete the folder `%LOCALAPPDATA%\IntelliFile`. Your own files are never touched.

## Changes and contact

IntelliFile is open-source software. Changes to this policy are published with the source code. Questions can be raised as an issue in the project's repository.
