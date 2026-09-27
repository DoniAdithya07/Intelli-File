/**
 * IntelliFile mark, "Found": a page on a graphite tile with a blue marker
 * dot on its corner, the file IntelliFile found for you. The blue is the
 * interface's marker colour. Flat colours only; the same drawing is
 * public/logo.svg and the source of the app icons (src-tauri/icons).
 */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <rect width="64" height="64" rx="15" fill="#15181D" />
      <rect x="0.75" y="0.75" width="62.5" height="62.5" rx="14.25" fill="none" stroke="#FFFFFF" strokeOpacity="0.14" strokeWidth="1.5" />
      <rect x="15" y="15" width="30" height="37" rx="5" fill="none" stroke="#FFFFFF" strokeWidth="4.5" />
      <rect x="22" y="27" width="16" height="3.5" rx="1.75" fill="#FFFFFF" />
      <rect x="22" y="35" width="11" height="3.5" rx="1.75" fill="#FFFFFF" fillOpacity="0.5" />
      <circle cx="45" cy="16" r="8.5" fill="#3B6FEA" stroke="#15181D" strokeWidth="3.5" />
    </svg>
  );
}
