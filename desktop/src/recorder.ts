import { encodeWav } from "./wavEncoder";

// Records microphone audio as raw PCM (via a ScriptProcessorNode — old
// API, but simple, synchronous, and universally supported in webviews;
// an AudioWorklet would be the modern choice but adds real complexity
// for no behavior difference at this scale) and produces a WAV blob the
// backend can read directly, no compressed-audio decoding needed.
export class Recorder {
  private audioContext: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private mute: GainNode | null = null;
  private chunks: Float32Array[] = [];
  private capturing = false;
  /** RMS of the most recent audio block, 0..1 — drives the live level bars in the UI. */
  level = 0;

  // How long the underlying audio pipeline takes to actually start
  // delivering real microphone samples, found via real-device testing
  // (not guessed): recordings consistently had 0.5-0.8s of literal
  // zero-valued silence at the start — not quiet room tone, exact
  // zero — even though onaudioprocess had already begun firing and the
  // UI had already told the user to start speaking. Since users
  // naturally start talking the instant they see "Recording," that gap
  // was silently eating the first word of every query, every time.
  // Fix: discard audio during this warm-up window, and don't resolve
  // start() (so the UI doesn't invite the user to speak) until it's over.
  private static readonly WARMUP_MS = 700;

  async start(): Promise<void> {
    this.chunks = [];
    this.capturing = false;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    try {
      this.startPipeline();
    } catch (e) {
      // If anything after getUserMedia fails, release the microphone —
      // otherwise the OS mic indicator stays on with nothing recording.
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
      throw e;
    }
    await new Promise((resolve) => setTimeout(resolve, Recorder.WARMUP_MS));
    this.capturing = true;
  }

  private startPipeline(): void {
    if (!this.stream) throw new Error("no microphone stream");
    this.audioContext = new AudioContext();
    this.source = this.audioContext.createMediaStreamSource(this.stream);
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);

    this.processor.onaudioprocess = (event) => {
      const block = event.inputBuffer.getChannelData(0);
      let sum = 0;
      for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
      this.level = Math.sqrt(sum / block.length);
      if (!this.capturing) return;
      this.chunks.push(new Float32Array(block));
    };

    this.source.connect(this.processor);
    // ScriptProcessorNode.onaudioprocess doesn't reliably fire in WebKit-based
    // webviews (Tauri on macOS) unless the node is wired into an active path
    // to destination. But connecting straight to destination plays the live
    // mic input out the speakers while recording — on a laptop, mic and
    // speakers are inches apart, so that's an acoustic feedback loop that
    // garbles the very audio being recorded. Route through a silent (gain=0)
    // node instead: keeps onaudioprocess firing, plays nothing audible.
    this.mute = this.audioContext.createGain();
    this.mute.gain.value = 0;
    this.processor.connect(this.mute);
    this.mute.connect(this.audioContext.destination);
  }

  stop(): Blob {
    const sampleRate = this.audioContext?.sampleRate ?? 44100;

    this.processor?.disconnect();
    this.mute?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.audioContext?.close();

    const totalLength = this.chunks.reduce((sum, c) => sum + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of this.chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    return encodeWav(merged, sampleRate);
  }
}
