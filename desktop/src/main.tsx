import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { OverlayApp } from "./overlay/OverlayApp";
import { initTheme } from "./theme";

initTheme(); // before the first paint, so the saved theme never flashes the other one

// The overlay window (src-tauri/tauri.conf.json) loads this same page with
// ?window=overlay; its body is transparent so only the search panel paints.
const isOverlay = new URLSearchParams(window.location.search).get("window") === "overlay";
if (isOverlay) document.body.classList.add("overlay-window");

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>{isOverlay ? <OverlayApp /> : <App />}</React.StrictMode>,
);
