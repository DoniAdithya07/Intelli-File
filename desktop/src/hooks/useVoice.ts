import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "../backend";
import { Recorder } from "../recorder";

// idle → warming (mic pipeline starting, ~0.7 s, we don't invite speech yet)
//      → recording (live level bars) → transcribing → idle, text delivered.
export type VoiceState = "idle" | "warming" | "recording" | "transcribing";

export function useVoice(onText: (text: string, heard?: string | null, suggestion?: string | null) => void, onError: (message: string) => void) {
  const [state, setState] = useState<VoiceState>("idle");
  const [level, setLevel] = useState(0);
  const recorderRef = useRef<Recorder | null>(null);

  // Sample the recorder's RMS ~20×/s while recording, for the bars.
  useEffect(() => {
    if (state !== "recording") return;
    const id = window.setInterval(() => setLevel(recorderRef.current?.level ?? 0), 50);
    return () => window.clearInterval(id);
  }, [state]);

  const toggle = useCallback(async () => {
    if (state === "idle") {
      setState("warming");
      try {
        const recorder = new Recorder();
        await recorder.start();
        recorderRef.current = recorder;
        setState("recording");
      } catch (e) {
        setState("idle");
        onError(e instanceof Error ? `Couldn't access the microphone: ${e.message}` : "Couldn't access the microphone.");
      }
      return;
    }
    if (state === "recording" && recorderRef.current) {
      const wav = recorderRef.current.stop();
      recorderRef.current = null;
      setState("transcribing");
      try {
        const res = await transcribeAudio(wav);
        if (res.error) onError(res.error);
        else if (res.text) onText(res.text, res.heard ?? null, res.suggestion ?? null);
        else onError("Didn't catch any words — try again, a little closer to the microphone.");
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
      } finally {
        setState("idle");
      }
    }
  }, [state, onText, onError]);

  return { state, level, toggle };
}
