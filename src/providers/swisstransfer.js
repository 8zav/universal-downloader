import { ProviderError } from '../core/errors.js';
import { decodeHtmlEntities } from '../core/html.js';
import { DEFAULT_USER_AGENT } from '../core/http.js';
import { createSession } from '../core/session.js';

const LINK_PATTERN = /swisstransfer\.com\/(?:[a-z]{2}\/)?(?:d|dl)\/([a-f0-9-]{36})/i;
const BASE = 'https://www.swisstransfer.com';

function findFileList(node) {
  if (Array.isArray(node)) {
    const looksLikeFiles = node.length > 0 && node.every((item) => item && typeof item === 'object' && item.id && (item.path || item.name));
    if (looksLikeFiles) return node;
    for (const item of node) {
      const found = findFileList(item);
      if (found) return found;
    }
    return null;
  }
  if (node && typeof node === 'object') {
    for (const value of Object.values(node)) {
      const found = findFileList(value);
      if (found) return found;
    }
  }
  return null;
}

async function readPage(response) {
  const body = await response.text();
  if (body.trimStart().startsWith('{')) return JSON.parse(body);
  const script =/<script\b[^>]*\bdata-page=["'][^"']*["'][^>]*>([\s\S]*?)<\/script>/i.exec(body)?.[1];
  const attribute = /\bdata-page="(\{[^"]*)"/.exec(body)?.[1];
  const json = script?.trim() || (attribute && decodeHtmlEntities(attribute));
  if (!json) throw new ProviderError('Page data not found (SwissTransfer layout changed?)', { provider: 'swisstransfer' });
  try {
    return JSON.parse(json);
  } catch {
    throw new ProviderError('Page data is not valid JSON (SwissTransfer layout changed?)', { provider: 'swisstransfer' });
  }
}

export const swisstransfer = {
  name: 'swisstransfer',

  matches(url) {
    return LINK_PATTERN.test(url);
  },

  async resolve(url, { fetch } = {}) {
    const linkId = LINK_PATTERN.exec(url)[1];
    const session = createSession(fetch, { 'User-Agent': DEFAULT_USER_AGENT });

    let page;
    try {
      page = await readPage(await session.send(`${BASE}/dl/${linkId}`, {
        headers: { Accept: 'text/html,application/xhtml+xml', Referer: `${BASE}/` },
      }));
    } catch (error) {
      if (error.status === 404) throw new ProviderError('Transfer not found (expired or deleted)', { provider: 'swisstransfer' });
      throw error;
    }

    const component = String(page?.component ?? '');
    if (/password/i.test(component)) {
      throw new ProviderError('This transfer is password protected (not supported yet)', { provider: 'swisstransfer' });
    }
    const files = findFileList(page?.props);
    if (!files) {
      throw new ProviderError(`No files in the page (${component || 'unknown page'})`, { provider: 'swisstransfer' });
    }

    session.setCookie('SWISSTRANSFER-CGU-ACCEPTED', 'true');
    return files.map((file) => {
      const fullPath = String(file.path ?? file.name);
      const segments = fullPath.split('/').filter(Boolean);
      return {
        id: String(file.id),
        url: `${BASE}/api/1/links/${linkId}/files/${file.id}`,
        filename: segments.pop(),
        subdir: segments.join('/') || undefined,
        size: Number(file.size) || undefined,
        resolveUrl: () => signedDownloadUrl(session, linkId, file.id),
      };
    });
  },
};

async function signedDownloadUrl(session, linkId, fileId) {
  const answer = await session.json(`${BASE}/api/1/links/${linkId}/files/${fileId}`, {
    headers: { Accept: 'application/json', Referer: `${BASE}/dl/${linkId}` },
  });
  if (answer?.result !== 'success' || !answer.data?.url) {
    throw new ProviderError('SwissTransfer did not return a download URL', { provider: 'swisstransfer' });
  }
  return { url: answer.data.url, headers: {} };
}
