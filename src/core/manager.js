import { EventEmitter } from 'node:events';
import { resolveSources } from '../providers/index.js';
import { Download } from './download.js';
import { formatDuration } from './format.js';

const DEFAULT_CONCURRENCY = 3;
const RELEASE_STATES = new Set(['paused', 'completed', 'failed', 'cancelled']);

export class DownloadManager extends EventEmitter {
  #concurrency;
  #providers;
  #defaults;
  #downloads = new Map();
  #pending = [];
  #active = new Set();

  constructor({ concurrency = DEFAULT_CONCURRENCY, providers = [], ...downloadDefaults } = {}) {
    super();
    this.#concurrency = Math.max(1, Math.floor(concurrency));
    this.#providers = providers;
    this.#defaults = downloadDefaults;
  }

  get downloads() {
    return [...this.#downloads.values()];
  }

  async add(url, overrides = {}) {
    const options = { ...this.#defaults, ...overrides };
    const context = { providers: this.#providers, fetch: options.fetch, password: options.password };
    const { provider, sources } = await resolveSources(url, context);
    const resolveAgain = async () => (await provider.resolve(url, context));

    const multiple = sources.length > 1;
    return sources.map((source) => {
      const { filename, password, ...downloadOptions } = options;
      return this.#enqueue(source, { ...downloadOptions, resolveAgain, ...(multiple ? {} : { filename }) });
    });
  }

  pause(download) {
    return download.pause();
  }

  resume(download) {
    if (download.state !== 'paused' || this.#pending.includes(download)) return false;
    this.#pending.unshift(download);
    this.#pump();
    return true;
  }

  pauseAll() {
    this.downloads.forEach((download) => download.pause());
  }

  resumeAll() {
    this.downloads.filter((download) => download.state === 'paused').forEach((download) => this.resume(download));
  }

  async cancelAll(options) {
    await Promise.all(this.downloads.map((download) => download.cancel(options)));
  }

  stats() {
    const snapshots = this.downloads.map((download) => download.progress);
    const count = (state) => snapshots.filter((snapshot) => snapshot.state === state).length;
    const unfinished = snapshots.filter((snapshot) => !['completed', 'failed', 'cancelled'].includes(snapshot.state));

    const speed = snapshots.reduce((sum, snapshot) => sum + snapshot.speed, 0);
    const remaining = unfinished.reduce((sum, snapshot) => sum + (snapshot.remaining ?? 0), 0);
    const eta = speed > 0 ? remaining / speed : null;

    return Object.freeze({
      active: count('downloading'),
      queued: count('queued'),
      paused: count('paused'),
      completed: count('completed'),
      failed: count('failed'),
      downloaded: snapshots.reduce((sum, snapshot) => sum + snapshot.downloaded, 0),
      remaining,
      speed,
      eta,
      etaText: eta === null ? '?' : formatDuration(eta),
    });
  }

  onIdle() {
    if (this.#isIdle()) return Promise.resolve();
    return new Promise((resolve) => this.once('idle', resolve));
  }

  #enqueue(source, options) {
    const download = new Download(source, options);
    this.#downloads.set(download.id, download);

    download.on('state', (state) => {
      this.emit('state', download, state);
      if (!RELEASE_STATES.has(state)) return;
      this.#active.delete(download);
      this.#pending = this.#pending.filter((queued) => queued !== download);
      this.#pump();
    });
    download.on('progress', (snapshot) => this.emit('progress', download, snapshot));
    download.on('completed', (filePath) => this.emit('completed', download, filePath));
    download.on('failed', (error) => this.emit('failed', download, error));

    this.#pending.push(download);
    this.emit('added', download);
    this.#pump();
    return download;
  }

  #pump() {
    while (this.#active.size < this.#concurrency && this.#pending.length > 0) {
      const download = this.#pending.shift();
      this.#active.add(download);
      if (download.state === 'paused') download.resume();
      else download.start().catch(() => {});
    }
    if (this.#isIdle()) this.emit('idle');
  }

  #isIdle() {
    return this.#active.size === 0 && this.#pending.length === 0;
  }
}
