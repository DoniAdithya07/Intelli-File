; IntelliFile setup: one .exe that unpacks the tested portable folder and opens the app.
;
; It installs for the current user only (no administrator password), into
; %LOCALAPPDATA%\Programs\IntelliFile, adds Start menu and desktop shortcuts and an
; entry in "Installed apps", and offers to open IntelliFile at the end. It never goes
; online: everything the app needs (search engine, models, sample folder) is inside.
;
; Built by backend\scripts\package_installer.py, which passes:
;   /DSRC=<the IntelliFile folder>  /DOUT=<the setup .exe>  /DVERSION=1.0.0  /DSIZE_KB=<installed size>

Unicode true
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor lzma
SetCompressorDictSize 64

!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP "IntelliFile"
!define EXE "IntelliFile.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntelliFile"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "MIT License"

!define MUI_ICON "..\src-tauri\icons\icon.ico"
!define MUI_UNICON "..\src-tauri\icons\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "Setup will install ${APP} ${VERSION} for you on this computer.$\r$\n$\r$\n${APP} finds your files by what is inside them. It works offline and only reads your files: it never changes, moves or deletes them.$\r$\n$\r$\nSetup needs about 2.6 GB of free space and no administrator password. Unpacking takes a few minutes."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APP} now"
!define MUI_FINISHPAGE_TEXT "${APP} is installed. The first start takes about 30 seconds while the search engine loads its models.$\r$\n$\r$\nYou can open it later from the Start menu or the desktop."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; IntelliFile must be closed while its files are replaced or removed: an open .exe cannot be written.
!macro CloseAppFirst
  retry:
    IfFileExists "$INSTDIR\${EXE}" 0 closed
    ClearErrors
    FileOpen $0 "$INSTDIR\${EXE}" a
    IfErrors 0 free
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${APP} is open. Close it, then click Retry." IDRETRY retry
      Abort
    free:
      FileClose $0
  closed:
!macroend

Section "Install"
  !insertmacro CloseAppFirst
  ; A newer version replaces the program files of the older one. What the user indexed is kept:
  ; it lives in %LOCALAPPDATA%\IntelliFile, which setup never touches.
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\models"
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\sample-folder"
  RMDir /r "$INSTDIR\licenses"
  SetOutPath "$INSTDIR"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${SIZE_KB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !insertmacro CloseAppFirst
  ; Only what setup put there, by name: never a blind delete of the chosen folder.
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\models"
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\sample-folder"
  RMDir /r "$INSTDIR\licenses"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\HOW_TO_RUN.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ; The index and settings in %LOCALAPPDATA%\IntelliFile are the user's: kept unless they say otherwise.
  IfSilent done
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete IntelliFile's index and settings on this computer?$\r$\n$\r$\nYour own files are not touched either way." IDNO done
    RMDir /r "$LOCALAPPDATA\IntelliFile"
  done:
SectionEnd
