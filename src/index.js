import { DownloadManager } from './core/manager.js';

export { Download } from './core/download.js';
export { DownloadManager } from './core/manager.js';
export { CancelledError, HttpError, ProviderError } from './core/errors.js';
export { formatBytes, formatDuration, formatSpeed } from './core/format.js';
export * from './providers/index.js';

export async function download(urls, options = {}) {
  const manager = new DownloadManager(options);
  const batches = await Promise.all([urls].flat().map((url) => manager.add(url)));
  return Promise.all(batches.flat().map((item) => item.wait()));
}
