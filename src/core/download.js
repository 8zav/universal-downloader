import { EventEmitter } from 'node:events';
import { createWriteStream } from 'node:fs';
import { mkdir, rename, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { CancelledError, HttpError, ProviderError, isRetryable } from './errors.js';
import { formatDuration } from './format.js';
import { DEFAULT_USER_AGENT, fileNameFromUrl, parseContentDisposition, sleep } from './http.js';
import { safeFileName, safeRelativeDir } from './paths.js';
import { SpeedTracker } from './speed-tracker.js';

const REFRESH_STATUSES = new Set([401, 403, 404, 410]);
const MAX_BACKOFF_MS = 30_000;
const MAX_RETRY_AFTER_MS = 60_000;
const BASE_BACKOFF_MS = 1_000;

const SETTLED_STATES = new Set(['completed', 'failed', 'cancelled']);

const DEFAULT_OPTIONS = {
  dir: '.',
  retries: 3,
  progressIntervalMs: 250,
};

function createDeferred() {
  const deferred = {};
  deferred.promise = new Promise((resolve, reject) => {
    deferred.resolve = resolve;
    deferred.reject = reject;
  });
  deferred.promise.catch(() => {});
  return deferred;
}

function writeChunk(stream, chunk) {
  return new Promise((resolve, reject) => {
    stream.write(chunk, (error) => (error ? reject(error) : resolve()));
  });
}

function closeStream(stream) {
  return new Promise((resolve) => stream.end(resolve));
}

async function sizeOf(filePath) {
  try {
    return (await stat(filePath)).size;
  } catch {
    return 0;
  }
}

function totalFromResponse(response, offset) {
  const range = /\/(\d+)$/.exec(response.headers.get('content-range') ?? '');
  if (range) return Number(range[1]);
  const length = Number(response.headers.get('content-length'));
  return Number.isFinite(length) && length > 0 ? offset + length : null;
}

export class Download extends EventEmitter {
  #source;
  #resolveAgain;
  #options;
  #state = 'queued';
  #intent = null;
  #controller = null;
  #loop = null;
  #downloaded = 0;
  #total;
  #tracker = new SpeedTracker();
  #lastEmit = -Infinity;
  #serverFileName = null;
  #filePath = null;
  #error = null;
  #waiter = createDeferred();

  constructor(source, { resolveAgain, ...options } = {}) {
    super();
    this.#source = source;
    this.#resolveAgain = resolveAgain;
    this.#options = { fetch: globalThis.fetch, ...DEFAULT_OPTIONS, ...options };
    this.#total = source.size ?? null;
    this.id = source.id ?? source.url;
  }

  get state() {
    return this.#state;
  }

  get error() {
    return this.#error;
  }

  get fileName() {
    return safeFileName(
      this.#options.filename ?? this.#source.filename ?? this.#serverFileName ?? fileNameFromUrl(this.#source.url),
    );
  }

  get filePath() {
    return path.join(this.#options.dir, safeRelativeDir(this.#source.subdir), this.fileName);
  }

  get progress() {
    const speed = this.#state === 'downloading' ? this.#tracker.bytesPerSecond : 0;
    const total = this.#total;
    const remaining = total === null ? null : Math.max(total - this.#downloaded, 0);
    const finished = this.#state === 'completed';
    const eta = finished ? 0 : remaining !== null && speed > 0 ? remaining / speed : null;
    return Object.freeze({
      id: this.id,
      state: this.#state,
      fileName: this.fileName,
      downloaded: this.#downloaded,
      total,
      percent: total ? Math.min((this.#downloaded / total) * 100, 100) : null,
      remaining,
      speed,
      eta,
      etaText: eta === null ? '?' : formatDuration(eta),
    });
  }

  start() {
    if (this.#state === 'queued') this.#launch();
    return this.wait();
  }

  wait() {
    return this.#waiter.promise;
  }

  pause() {
    if (this.#state === 'queued') {
      this.#setState('paused');
      return true;
    }
    if (this.#state !== 'downloading') return false;
    this.#intent = 'pause';
    this.#controller?.abort();
    this.#setState('paused');
    return true;
  }

  async resume() {
    if (this.#state !== 'paused') return false;
    await this.#loop;
    if (this.#state !== 'paused') return false;
    this.#launch();
    return true;
  }

  async cancel({ deleteFile = true } = {}) {
    if (SETTLED_STATES.has(this.#state)) return false;
    this.#intent = 'cancel';
    this.#controller?.abort();
    this.#setState('cancelled');
    this.#waiter.reject(new CancelledError());
    await this.#loop;
    if (deleteFile) await rm(`${this.filePath}.part`, { force: true });
    return true;
  }

  #launch() {
    this.#intent = null;
    this.#tracker.reset();
    this.#lastEmit = -Infinity;
    this.#setState('downloading');
    this.#loop = this.#runWithRetries().then(
      (filePath) => this.#settle(filePath, null),
      (error) => this.#settle(null, error),
    );
  }

  #settle(filePath, error) {
    if (this.#intent) return;
    if (error) {
      this.#error = error;
      this.#setState('failed');
      this.emit('failed', error);
      this.#waiter.reject(error);
      return;
    }
    this.#filePath = filePath;
    if (this.#total === null) this.#total = this.#downloaded;
    this.#downloaded = this.#total;
    this.#setState('completed');
    this.emit('progress', this.progress);
    this.emit('completed', filePath);
    this.#waiter.resolve(filePath);
  }

  #setState(state) {
    if (this.#state === state) return;
    this.#state = state;
    this.emit('state', state);
  }

  async #runWithRetries() {
    let attempt = 0;
    let refreshed = false;
    for (;;) {
      try {
        return await this.#transfer();
      } catch (error) {
        if (this.#intent) return null;
        if (!refreshed && this.#resolveAgain && error instanceof HttpError && REFRESH_STATUSES.has(error.status)) {
          refreshed = true;
          await this.#refreshSource();
          continue;
        }
        attempt += 1;
        if (attempt > this.#options.retries || !isRetryable(error)) throw error;
        await sleep(this.#backoffMs(attempt, error));
        if (this.#intent) return null;
      }
    }
  }

  #backoffMs(attempt, error) {
    if (error instanceof HttpError && error.retryAfterSeconds !== null) {
      return Math.min(error.retryAfterSeconds * 1000, MAX_RETRY_AFTER_MS);
    }
    return Math.min(BASE_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS) + Math.random() * 500;
  }

  async #refreshSource() {
    const sources = await this.#resolveAgain();
    const match = sources.find((candidate) => candidate.id === this.#source.id) ?? (sources.length === 1 ? sources[0] : null);
    if (!match) throw new ProviderError('The file is no longer available at the source');
    this.#source = { ...this.#source, ...match };
  }

  async #transfer() {
    const partPath = `${this.filePath}.part`;
    await mkdir(path.dirname(partPath), { recursive: true });

    let offset = await sizeOf(partPath);
    this.#controller = new AbortController();
    const target = await this.#locate();
    const headers = {
      'User-Agent': DEFAULT_USER_AGENT,
      'Accept-Encoding': 'identity',
      ...target.headers,
      ...(offset > 0 ? { Range: `bytes=${offset}-` } : {}),
    };

    const response = await this.#options.fetch(target.url, {
      headers,
      redirect: 'follow',
      signal: this.#controller.signal,
    });

    if (response.status === 416 && offset > 0) {
      this.#downloaded = offset;
      return this.#finalize(partPath);
    }
    if (!response.ok) throw new HttpError(response.status, response.statusText, target.url);
    this.#rejectWebPage(response);

    if (offset > 0 && response.status !== 206) offset = 0;
    this.#serverFileName ??=parseContentDisposition(response.headers.get('content-disposition'));
    this.#downloaded = offset;
    this.#total = totalFromResponse(response, offset) ?? this.#source.size ?? null;

    this.#tracker.record(this.#downloaded);
    const output = createWriteStream(partPath, { flags: offset > 0 ? 'a' : 'w' });
    output.on('error', () => {});
    try {
      for await (const chunk of response.body) {
        await writeChunk(output, chunk);
        this.#downloaded += chunk.length;
        this.#tick();
      }
    } finally {
      await closeStream(output);
    }

    if (this.#total !== null && this.#downloaded < this.#total) {
      throw new Error(`Connection closed early (${this.#downloaded}/${this.#total} bytes)`);
    }
    return this.#finalize(partPath);
  }

  async #locate() {
    const { resolveUrl, url, headers } = this.#source;
    if (!resolveUrl) return { url, headers };
    const located = await resolveUrl({ fetch: this.#options.fetch });
    return { url: located.url, headers: located.headers ?? {} };
  }

  #rejectWebPage(response) {
    const type = response.headers.get('content-type') ?? '';
    const isWebContent = /^(text\/html|application\/json)/i.test(type);
    const expectsIt = this.#options.allowHtml || /\.(html?|json)$/i.test(this.fileName);
    if (isWebContent && !expectsIt) {
      throw new ProviderError(
        'The server returned a web page or API response instead of the file (link expired, private, or the host changed its API)',
      );
    }
  }

  async #finalize(partPath) {
    const destination = this.filePath;
    await rename(partPath, destination);
    return destination;
  }

  #tick() {
    const now = performance.now();
    if (now - this.#lastEmit < this.#options.progressIntervalMs) return;
    this.#lastEmit = now;
    this.#tracker.record(this.#downloaded, now);
    this.emit('progress', this.progress);
  }
}
