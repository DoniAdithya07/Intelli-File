---
name: IntelliFile Obsidian
colors:
  surface: '#11131b'
  surface-dim: '#11131b'
  surface-bright: '#373942'
  surface-container-lowest: '#0c0e16'
  surface-container-low: '#191b24'
  surface-container: '#1d1f28'
  surface-container-high: '#282a32'
  surface-container-highest: '#33343d'
  on-surface: '#e2e1ed'
  on-surface-variant: '#bbcac6'
  inverse-surface: '#e2e1ed'
  inverse-on-surface: '#2e3039'
  outline: '#859491'
  outline-variant: '#3c4947'
  surface-tint: '#4fdbcc'
  primary: '#a3fff3'
  on-primary: '#003732'
  primary-container: '#5ee7d8'
  on-primary-container: '#00665d'
  inverse-primary: '#006a62'
  secondary: '#b4c5ff'
  on-secondary: '#002979'
  secondary-container: '#163f9e'
  on-secondary-container: '#9bb2ff'
  tertiary: '#ecedff'
  on-tertiary: '#2b3043'
  tertiary-container: '#cdd1eb'
  on-tertiary-container: '#54596f'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#71f8e8'
  primary-fixed-dim: '#4fdbcc'
  on-primary-fixed: '#00201d'
  on-primary-fixed-variant: '#005049'
  secondary-fixed: '#dbe1ff'
  secondary-fixed-dim: '#b4c5ff'
  on-secondary-fixed: '#00174c'
  on-secondary-fixed-variant: '#163f9e'
  tertiary-fixed: '#dde1fb'
  tertiary-fixed-dim: '#c1c5df'
  on-tertiary-fixed: '#161b2d'
  on-tertiary-fixed-variant: '#41465b'
  background: '#11131b'
  on-background: '#e2e1ed'
  surface-variant: '#33343d'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 1.75rem
    fontWeight: '600'
    lineHeight: 2.25rem
    letterSpacing: -0.025em
  headline-sm:
    fontFamily: Inter
    fontSize: 1.125rem
    fontWeight: '600'
    lineHeight: 1.5rem
    letterSpacing: -0.015em
  body-lg:
    fontFamily: Inter
    fontSize: 0.9375rem
    fontWeight: '400'
    lineHeight: 1.45rem
    letterSpacing: -0.01em
  body-sm:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.25rem
    letterSpacing: '0'
  label-mono:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '500'
    lineHeight: 0.875rem
    letterSpacing: 0.04em
  label-shortcut:
    fontFamily: JetBrains Mono
    fontSize: 0.625rem
    fontWeight: '500'
    lineHeight: 0.75rem
    letterSpacing: 0.02em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system establishes a high-performance, intelligent desktop environment crafted for power users, engineers, and digital minimalists. Inspired by the meticulous utility of Linear and the ambient responsiveness of Raycast and Spotlight, the aesthetic blends dark-mode minimalism with refined glassmorphism and surgical precision.

The emotional signature is calm authority, extreme responsiveness, and quiet intelligence. Surfaces feel sculpted from deep optical obsidian and tinted aerospace glass, allowing file contents, previews, and AI synthesis layers to take primary focus without visual friction.

### Design Principles
- **Restraint Over Spectacle:** Vibrant gradients are strictly reserved for state signals, active focus rings, and high-value AI operations. The canvas remains neutral, dark, and composed.
- **Micro-Precision Engineering:** Every surface is delineated by 1px sub-pixel outlines rather than harsh shadows. Layouts align strictly to structural data grids.
- **Spatial Depth via Translucency:** Structural hierarchies rely on physical stacking orders, subtle background blurs, and glass refraction rather than aggressive elevation tiers.

## Colors

The foundation is built on deep cosmic charcoals and navy-blacks that prevent OLED smearing while maintaining lower eye fatigue than pure `#000000`.

### Surface & Panel Roles
- **Base Canvas (Root Window):** `#0f1119`
- **Sidebar & Inactive Shell:** `#121520` (subtle native blur with `backdrop-filter: blur(24px)`)
- **Main Content Surface:** `#151824`
- **Floating Overlays & Command Palette:** `#1a1e2e` (with `backdrop-filter: blur(32px)`)
- **Interactive Tonal Hover:** `rgba(255, 255, 255, 0.04)`
- **Selected Surface:** `rgba(124, 156, 255, 0.10)`

### Accents & Gradient Physics
- **Gradient Vector:** Linear 135deg from Teal (`#5ee7d8`) to Indigo (`#7c9cff`).
- **Accent Rules:** Never flood large surfaces with the primary teal or secondary indigo. Use as 1px interior glow borders, hairline focus rings, micro badge text highlights, active command markers, and AI generation pulse rings.
- **Borders & Dividers:** `rgba(255, 255, 255, 0.07)` default hairline; `rgba(255, 255, 255, 0.12)` for panel boundaries; `#23262e` for opaque fallbacks.

## Typography

The typography couples the universal functional legibility of **Inter** for primary UI narratives with the computational density of **JetBrains Mono** for structural file metadata.

### Typographic Discipline
- **Inter (UI & Prose):** Tuned with negative tracking at larger weights to produce the tight, mechanical profile common in elite developer interfaces. Body text is prioritized for fast scanning across file listings and multi-column inspector panes.
- **JetBrains Mono (Metadata & Controls):** Used strictly for file extensions (`.PDF`, `.TSX`, `.M4A`), sizes, Unix permissions, hashes, and hotkey accelerators (`⌘K`, `⇧⌥P`). Rendered uppercase for extensions and key sequences.

## Layout & Spacing

The default viewport is anchored around standard macOS/Windows desktop proportions of **1100px width × 720px height**, scaling fluidly within constrained aspect ratios up to 4K displays.

### Layout Topology
- **Window Shell:** Integrates frameless desktop window chrome. Top left reserves a 72px width draggable hit region for macOS window controls (traffic lights) with 12px vertical alignment.
- **Left Sidebar:** Slim navigational panel, fixed at 220px (collapsible to 56px icon rail).
- **Core Viewport:** Dynamic 2-column or 3-column pane system.
  - *Primary File Explorer:* Fluid width with fixed minimum (380px).
  - *Contextual AI & Preview Pane:* Fixed 340px right dock with slide-over collapse.
- **Spatial Rhythm:** A strict 4px sub-grid guides element positioning. Compact internal padding (`space-xs` and `space-sm`) enforces high information density while generous structural gutters (`gutter`) prevent visual overwhelm.

## Elevation & Depth

Visual hierarchy is articulated through atmospheric luminosity and optical edge definition rather than physical drop shadows.

### Depth Layers
1. **Desktop Canvas (Root):** `#0f1119` with zero shadow.
2. **Divided Panels (Sidebar/Inspector):** Stacked immediately above root with `1px solid rgba(255, 255, 255, 0.07)` separator borders and `backdrop-filter: blur(20px)`.
3. **Interactive Floating Cards (List items, Grid tiles):** Transparent at rest; on hover they inherit `rgba(255, 255, 255, 0.03)` fill with `box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06)`.
4. **Command Palette & AI Modal Overlays:** Elevated to Z-Index 100 with double-ring borders: an inner `1px solid rgba(255, 255, 255, 0.1)` and an outer ambient cast of `box-shadow: 0 24px 48px -12px rgba(0, 0, 0, 0.7), 0 0 0 1px rgba(124, 156, 255, 0.15)`.

## Shapes

The interface balances soft industrial edges with high structural discipline. 

- **Outer App Window:** `14px` border radius clipped via native shell boundaries.
- **Overlay Windows & Command Bar:** `12px` border radius (`rounded-lg` equivalent).
- **Inner Panels, File Cards, & List Selections:** `8px` (`0.5rem`, level 2).
- **File Type Badges & Keyboard Badges:** `4px` to maintain rectangular stability within monospace strings.

## Components

### Buttons & Quick Actions
- **Ghost Action (Standard):** Translucent background, `0.5rem` radius, `0.75rem` vertical padding. Inactive text is `rgba(255,255,255,0.6)`. Hover elevates text to pure `#ffffff` and background to `rgba(255,255,255,0.06)`.
- **Primary AI Trigger:** Styled with an edge highlight using the signature gradient. Background is `#151824`, surrounded by an inner gradient stroke (`#5ee7d8` to `#7c9cff`), with interactive glow on focus.

### File Badges & Monospace Tags
- **File Type Micro-Badges:** Ultra-compact badges featuring `label-mono` font. 
  - *Audio/Video (`M4A`, `MOV`):* Background `rgba(124, 156, 255, 0.12)`, text `#7c9cff`.
  - *Text/Code (`MD`, `TS`):* Background `rgba(94, 231, 216, 0.12)`, text `#5ee7d8`.
  - *Documents (`PDF`, `DOC`):* Background `rgba(255, 255, 255, 0.08)`, text `rgba(255, 255, 255, 0.85)`.
- **Keyboard Shortcuts:** Encased in micro-pill containers (`padding: 2px 5px`, `border: 1px solid rgba(255,255,255,0.12)`, `background: rgba(255,255,255,0.03)`).

### Explorer List Rows & Grid Cells
- **Row Architecture:** 32px height in compact mode, 40px in standard. Left edge features 16px file icon, followed by primary title (`body-sm`), followed by inline AI summary chip and terminal monospace metadata.
- **Selection State:** Active selection renders `rgba(124, 156, 255, 0.12)` background, with a left indicator pill rendered in the linear accent gradient.

### Input Fields & Command Center
- **Omnibox / Search:** Clean borderless field housed in `rgba(255, 255, 255, 0.03)`. Displays `⌘K` accelerator badge pinned to the right edge. On focus, triggers an ambient teal-to-indigo outer diffusion glow (`0 0 16px rgba(94, 231, 216, 0.15)`).

### AI Preview Inspector Card
- **Structure:** Anchored in the right sidebar. Displays an instant contextual summary generated via background analysis, semantic file tags, token breakdown, and quick action macros (e.g., "Summarize", "Transcribe", "Compress").