const DEFAULT_WINDOW_MS = 5000;

export class SpeedTracker {
  #windowMs;
  #samples = [];

  constructor(windowMs = DEFAULT_WINDOW_MS) {
    this.#windowMs = windowMs;
  }

  record(totalBytes, now = performance.now()) {
    this.#samples.push({ time: now, bytes: totalBytes });
    const cutoff = now - this.#windowMs;
    while (this.#samples.length > 2 && this.#samples[1].time < cutoff) {
      this.#samples.shift();
    }
  }

  get bytesPerSecond() {
    if (this.#samples.length < 2) return 0;
    const first = this.#samples[0];
    const last = this.#samples[this.#samples.length - 1];
    const seconds = (last.time - first.time) / 1000;
    return seconds > 0 ? (last.bytes - first.bytes) / seconds : 0;
  }

  reset() {
    this.#samples = [];
  }
}
