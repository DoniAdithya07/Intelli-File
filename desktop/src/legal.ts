/**
 * The privacy policy and terms of use shown in Settings > About. The same
 * text is in PRIVACY.md and TERMS.md at the repository root; keep them in
 * step. Every statement here describes what the code actually does.
 */
export interface LegalSection { heading: string; body: string[] }
export interface LegalDoc { title: string; updated: string; sections: LegalSection[] }

export const PRIVACY: LegalDoc = {
  title: "Privacy policy",
  updated: "26 September 2026",
  sections: [
    {
      heading: "The short version",
      body: [
        "IntelliFile runs entirely on your computer. It has no account, makes no internet connections, and sends nothing anywhere: no telemetry, no analytics, no crash reports, no update checks.",
      ],
    },
    {
      heading: "What IntelliFile reads",
      body: [
        "Only the folders and files you allow on the first-run screen or in Folders. You can change this at any time in Settings, under File access.",
        "It reads files to index them. It never changes, moves, renames or deletes your files. It opens a file only when you ask it to.",
        "If you switch on Learn from files you opened in Windows, it also reads the Recent items list Windows keeps for your account. Only entries for files in your indexed folders are used. Switching the option off removes everything it imported.",
      ],
    },
    {
      heading: "What IntelliFile stores, and where",
      body: [
        "Everything is kept in %LOCALAPPDATA%\\IntelliFile on this computer: the search index (text passages, their search vectors, and a list of your indexed files with paths, sizes, dates and content fingerprints), your settings, and log files.",
        "If Remember my activity is on, it also keeps a history of your searches and of the files you open or show in File Explorer from IntelliFile. This history personalizes your results. Switch it off in Settings to stop recording; Clear activity deletes it.",
        "Photo thumbnails are made when shown and are not saved.",
      ],
    },
    {
      heading: "Voice search",
      body: [
        "The microphone is used only between your click to start recording and your click to stop. The audio is turned into text on this computer, in memory, and is not saved. Only the resulting search text can be kept, as part of your activity history.",
      ],
    },
    {
      heading: "The local connection",
      body: [
        "The app window talks to its search engine through a connection that only this computer can reach (127.0.0.1), protected by a key that changes every time IntelliFile starts.",
      ],
    },
    {
      heading: "Removing your data",
      body: [
        "Remove a folder in Folders to delete its part of the index. Clear activity in Settings deletes your history. To remove everything, quit IntelliFile and delete the folder %LOCALAPPDATA%\\IntelliFile. Your own files are never touched.",
      ],
    },
    {
      heading: "Changes and contact",
      body: [
        "IntelliFile is open-source software. Changes to this policy are published with the source code. Questions can be raised as an issue in the project's repository.",
      ],
    },
  ],
};

export const TERMS: LegalDoc = {
  title: "Terms of use",
  updated: "26 September 2026",
  sections: [
    {
      heading: "Licence",
      body: [
        "IntelliFile is free, open-source software released under the MIT License. You may use, copy, change and share it under that licence. The full text is in the LICENSE file that comes with the source code.",
      ],
    },
    {
      heading: "Provided as is",
      body: [
        "IntelliFile is provided as is, without warranty of any kind. The authors are not liable for any claim, damage or other liability arising from its use.",
      ],
    },
    {
      heading: "Answers can be wrong",
      body: [
        "Search results and Ask answers are produced automatically by models running on your computer. They can be incomplete or wrong. Every answer cites the files it used; check those files before relying on an answer.",
      ],
    },
    {
      heading: "Your files and your responsibility",
      body: [
        "You decide which folders IntelliFile may read. Only index files you are allowed to access. IntelliFile does not change your files, but you remain responsible for keeping your own backups.",
      ],
    },
    {
      heading: "Third-party components",
      body: [
        "IntelliFile includes models, fonts and libraries made by others, each under its own licence (for example Apache 2.0, MIT, SIL Open Font License and CC BY-SA 4.0). They are listed in THIRD_PARTY_NOTICES.md with the source code.",
      ],
    },
  ],
};
