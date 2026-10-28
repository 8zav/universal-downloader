import { ProviderError } from '../core/errors.js';
import { decodeHtmlEntities, findTag, readAttribute } from '../core/html.js';
import { fileNameFromUrl, getJson, getText } from '../core/http.js';

const HOST_PATTERN = /^https?:\/\/(?:[\w-]+\.)?mediafire\.com\//i;
const DIRECT_HOST_PATTERN = /^https?:\/\/download\d*\.mediafire\.com\//i;
const FOLDER_PATTERN = /mediafire\.com\/folder\/([A-Za-z0-9]+)/i;
const BUTTON_PATTERN = /<a\b[^>]*(?:id\s*=\s*["']downloadButton["']|class\s*=\s*["'][^"']*\bpopsok\b[^"']*["'])[^>]*>/i;
const FOLDER_API = 'https://www.mediafire.com/api/1.5/folder/get_content.php';
const FOLDER_CHUNK_SIZE = 100;
const MAX_FOLDER_DEPTH = 8;
const PAGE_RESOLVE_CONCURRENCY = 4;

export function extractDownloadLink(html) {
  const button = findTag(html, BUTTON_PATTERN);
  if (!button) return null;

  const scrambled = readAttribute(button, 'data-scrambled-url');
  if (scrambled) {
    const decoded = Buffer.from(scrambled, 'base64').toString('utf8');
    if (/^https?:\/\//i.test(decoded)) return decoded;
  }
  const href = readAttribute(button, 'href');
  return href && /^https?:\/\//i.test(href) ? decodeHtmlEntities(href) : null;
}

async function resolveFilePage(pageUrl, fetch) {
  const link = extractDownloadLink(await getText(pageUrl, { fetch }));
  if (!link) {
    throw new ProviderError('Download button not found (file removed or page layout changed)', { provider: 'mediafire' });
  }
  return { id: link, url: link, filename: fileNameFromUrl(link) ?? undefined, headers: { Referer: pageUrl } };
}

async function listFolder(folderKey, { fetch, subdir = '', depth = 0, found = [] }) {
  const fetchPage = async (contentType, chunk) => {
    const query = new URLSearchParams({
      folder_key: folderKey,
      content_type: contentType,
      chunk: String(chunk),
      chunk_size: String(FOLDER_CHUNK_SIZE),
      response_format: 'json',
    });
    const { response } = await getJson(`${FOLDER_API}?${query}`, { fetch });
    if (response?.result !== 'Success') {
      throw new ProviderError(response?.message ?? 'Folder unavailable', { provider: 'mediafire' });
    }
    return response.folder_content;
  };

  for (let chunk = 1, more = true; more; chunk += 1) {
    const content = await fetchPage('files', chunk);
    for (const file of content.files ?? []) {
      found.push({ pageUrl: file.links?.normal_download, name: file.filename, size: Number(file.size) || undefined, subdir });
    }
    more = content.more_chunks === 'yes';
  }

  if (depth < MAX_FOLDER_DEPTH) {
    for (let chunk = 1, more = true; more; chunk += 1) {
      const content = await fetchPage('folders', chunk);
      for (const folder of content.folders ?? []) {
        await listFolder(folder.folderkey, { fetch, subdir: [subdir, folder.name].filter(Boolean).join('/'), depth: depth + 1, found });
      }
      more = content.more_chunks === 'yes';
    }
  }
  return found;
}

async function mapWithConcurrency(items, limit, task) {
  const results = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await task(items[index]);
    }
  });
  await Promise.all(workers);
  return results;
}

export const mediafire = {
  name: 'mediafire',

  matches(url) {
    return HOST_PATTERN.test(url);
  },

  async resolve(url, { fetch } = {}) {
    if (DIRECT_HOST_PATTERN.test(url)) {
      return [{ id: url, url, filename: fileNameFromUrl(url) ?? undefined, headers: { Referer: 'https://www.mediafire.com/' } }];
    }

    const folderKey = FOLDER_PATTERN.exec(url)?.[1];
    if (folderKey) {
      const entries = (await listFolder(folderKey, { fetch })).filter((entry) => entry.pageUrl);
      if (entries.length === 0) throw new ProviderError('The folder is empty', { provider: 'mediafire' });
      return mapWithConcurrency(entries, PAGE_RESOLVE_CONCURRENCY, async (entry) => ({
        ...(await resolveFilePage(entry.pageUrl, fetch)),
        filename: entry.name,
        size: entry.size,
        subdir: entry.subdir || undefined,
      }));
    }

    return [await resolveFilePage(url, fetch)];
  },
};
