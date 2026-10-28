import { ProviderError } from '../core/errors.js';
import { decodeHtmlEntities, findTag, readAttribute } from '../core/html.js';
import { parseContentDisposition } from '../core/http.js';
import { createSession } from '../core/session.js';

const FILE_ID_PATTERNS = [/drive\.google\.com\/file\/d\/([\w-]+)/i, /[?&]id=([\w-]+)/i];
const HOST_PATTERN = /^https?:\/\/(?:drive|docs)\.google\.com\//i;
const DOWNLOAD_URL = 'https://drive.usercontent.google.com/download';

function extractFileId(url) {
  for (const pattern of FILE_ID_PATTERNS) {
    const match = pattern.exec(url);
    if (match) return match[1];
  }
  return null;
}

function parseConfirmForm(html) {
  const form = findTag(html, /<form\b[^>]*id=["']download-form["'][^>]*>/i);
  if (!form) return null;
  const params = new URLSearchParams();
  for (const input of html.matchAll(/<input\b[^>]*type=["']hidden["'][^>]*>/gi)) {
    const name = readAttribute(input[0], 'name');
    if (name) params.set(name, decodeHtmlEntities(readAttribute(input[0], 'value') ?? ''));
  }
  const action = decodeHtmlEntities(readAttribute(form, 'action') ?? DOWNLOAD_URL);
  return `${action}?${params}`;
}

export const googledrive = {
  name: 'googledrive',

  matches(url) {
    return HOST_PATTERN.test(url);
  },

  async resolve(url, { fetch } = {}) {
    if (/\/drive\/folders\//i.test(url)) {
      throw new ProviderError('Google Drive folders need an API key and are not supported, use single file links', {
        provider: 'googledrive',
      });
    }
    const fileId = extractFileId(url);
    if (!fileId) throw new ProviderError('Could not find the file id in this link', { provider: 'googledrive' });

    const session = createSession(fetch);
    const firstUrl = `${DOWNLOAD_URL}?${new URLSearchParams({ id: fileId, export: 'download', confirm: 't' })}`;
    let response = await session.send(firstUrl);
    let finalUrl = firstUrl;

    if ((response.headers.get('content-type') ?? '').includes('text/html')) {
      const html = await response.text();
      finalUrl = parseConfirmForm(html);
      if (!finalUrl) {
        throw new ProviderError('File not shared publicly, download quota exceeded, or removed', { provider: 'googledrive' });
      }
      response = await session.send(finalUrl);
    }

    const size = Number(response.headers.get('content-length')) || undefined;
    const filename = parseContentDisposition(response.headers.get('content-disposition')) ?? undefined;
    await response.body?.cancel();

    const cookie = session.cookieHeader();
    return [{ id: fileId, url: finalUrl, filename, size, headers: cookie ? { Cookie: cookie } : {} }];
  },
};
