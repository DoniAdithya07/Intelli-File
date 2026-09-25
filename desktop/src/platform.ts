// The app ships on both macOS and Windows; only the wording differs.
export const IS_MAC = /Mac/i.test(navigator.platform) || /Macintosh/i.test(navigator.userAgent);
export const MOD_KEY = IS_MAC ? "⌘" : "Ctrl";
export const FILE_MANAGER = IS_MAC ? "Finder" : "Explorer";
