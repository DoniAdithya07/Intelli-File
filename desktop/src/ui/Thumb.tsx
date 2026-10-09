import { ImgHTMLAttributes, useEffect, useRef, useState } from "react";
import { fetchThumbnail } from "../backend";

type ThumbProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  path: string; size?: number; at?: number | null;
  /** Classes for the fixed-size box shown until the picture arrives. Defaults to the image's own classes. */
  boxClassName?: string;
};

/**
 * An <img> that loads its picture with the API token header once it is near the
 * screen, and frees the blob URL when it goes away. Until then (and if it fails)
 * it shows a box of the same size, so nothing jumps when the picture arrives.
 */
export function Thumb({ path, size, at, onError, boxClassName, className, ...img }: ThumbProps) {
  const box = useRef<HTMLSpanElement>(null);
  const [near, setNear] = useState(false);
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  // Keep the newest onError without making it a dependency (an inline arrow would reload the picture every render).
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  // Start loading only when the box is within 200 px of the viewport.
  useEffect(() => {
    const el = box.current;
    if (near || !el) return;
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) setNear(true); }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);

  useEffect(() => {
    if (!near) return;
    const ctl = new AbortController();
    let made: string | null = null;
    setSrc(null);
    setFailed(false);
    fetchThumbnail(path, size, at ?? null, ctl.signal)
      .then((url) => { if (ctl.signal.aborted) URL.revokeObjectURL(url); else { made = url; setSrc(url); } })
      .catch((e) => { if (!ctl.signal.aborted) { setFailed(true); (onErrorRef.current as ((e: unknown) => void) | undefined)?.(e); } });
    return () => { ctl.abort(); if (made) URL.revokeObjectURL(made); };
  }, [near, path, size, at]);

  if (src) return <img src={src} className={className} {...img} />;
  return (
    <span ref={box} aria-hidden className={`${failed ? "" : "skeleton "}grid place-items-center overflow-hidden text-center text-[11px] text-ink/65 ${boxClassName ?? className ?? ""}`}>
      {failed && "No preview"}
    </span>
  );
}
