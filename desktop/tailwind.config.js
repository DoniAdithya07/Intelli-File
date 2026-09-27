/**
 * IntelliFile design tokens, after Windows 11 File Explorer: a grey window,
 * the content in a rounded panel, Windows blue for actions and selection,
 * amber only for personalization. No gradients, no glows.
 *
 * Every colour is a CSS variable holding "R G B" channels (App.css), so the
 * Day and Night themes swap them and opacity modifiers such as `text-ink/60`
 * keep working in both.
 */
const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: c("canvas"),       // window background
        shell: c("shell"),         // the window and the navigation pane
        content: c("content"),     // cards and panels
        overlay: c("content"),     // quick-search window
        ink: c("ink"),             // text; lighter text is ink at an opacity
        rule: c("rule"),           // 1px lines
        "rule-strong": c("rule-strong"),
        accent: c("accent"),       // Windows blue: the only action colour
        "on-accent": c("on-accent"),
        "accent-soft": c("accent-soft"),
        marker: c("marker"),       // "found" and "you are here"
        amber: c("amber"),         // personalization reasons
        "amber-soft": c("amber-soft"),
        error: c("error"),
        "error-soft": c("error-soft"),
      },
      fontFamily: {
        sans: ['"Segoe UI Variable Text"', '"Segoe UI"', "system-ui", "sans-serif"],
        mono: ['"Cascadia Mono"', "Consolas", "monospace"],
      },
      borderRadius: { DEFAULT: "6px", md: "6px", lg: "8px", xl: "10px", "2xl": "12px" },
      boxShadow: {
        palette: "0 12px 32px -8px rgb(0 0 0 / 0.28), 0 0 0 1px rgb(var(--c-rule))",
      },
    },
  },
  plugins: [],
};
