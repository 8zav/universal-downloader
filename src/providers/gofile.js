import { createHash } from 'node:crypto';
import { ProviderError } from '../core/errors.js';
import { DEFAULT_USER_AGENT, getJson, postJson } from '../core/http.js';

const LINK_PATTERN = /gofile\.io\/d\/([A-Za-z0-9]+)/i;
const API = 'https://api.gofile.io';
const TOKEN_TTL_MS = 2 * 60 * 60 * 1000;
const WEBSITE_TOKEN_SECRET = '5d4f7g8sd45fsd';
const WEBSITE_TOKEN_SLOT_SECONDS = 4 * 60 * 60;
const WEBSITE_LANGUAGE = 'en-US';

let cachedToken = null;

export function buildWebsiteToken(accountToken, userAgent = DEFAULT_USER_AGENT, now = Date.now()) {
  const slot = Math.floor(now / 1000 / WEBSITE_TOKEN_SLOT_SECONDS);
  return createHash('sha256')
    .update(`${userAgent}::${WEBSITE_LANGUAGE}::${accountToken}::${slot}::${WEBSITE_TOKEN_SECRET}`)
    .digest('hex');
}

async function getGuestToken(fetch) {
  if (cachedToken && Date.now() - cachedToken.createdAt < TOKEN_TTL_MS) return cachedToken.value;

  const response = await postJson(`${API}/accounts`, { fetch, headers: { Referer: 'https://gofile.io/' } });
  const value = response?.data?.token;
  if (response?.status !== 'ok' || !value) {
    throw new ProviderError(`Could not create a guest account (${response?.status ?? 'unknown'})`, { provider: 'gofile' });
  }
  cachedToken = { value, createdAt: Date.now() };
  return value;
}

async function listFolder(folderId, { fetch, token, password, subdir, files, signal }) {
  const query = new URLSearchParams({ wt: buildWebsiteToken(token) });
  if (password) query.set('password', createHash('sha256').update(password).digest('hex'));

  const response = await getJson(`${API}/contents/${folderId}?${query}`, {
    fetch,
    headers: { Authorization: `Bearer ${token}`, 'X-Website-Token': buildWebsiteToken(token), Referer: 'https://gofile.io/' },
  });
  if (response?.status !== 'ok') {
    throw new ProviderError(`Folder unavailable: ${response?.status ?? 'unknown error'}`, { provider: 'gofile' });
  }

  const children = Object.values(response.data?.children ?? {});
  for (const child of children) {
    if (child.type === 'folder') {
      await listFolder(child.id, { fetch, token, password, subdir: [subdir, child.name].filter(Boolean).join('/'), files, signal });
    } else if (child.link) {
      files.push({
        id: child.id,
        url: child.link,
        filename: child.name,
        subdir: subdir || undefined,
        size: Number(child.size) || undefined,
        headers: { Cookie: `accountToken=${token}`, Authorization: `Bearer ${token}`, Referer: 'https://gofile.io/' },
      });
    }
  }
}

export const gofile = {
  name: 'gofile',

  matches(url) {
    return LINK_PATTERN.test(url);
  },

  async resolve(url, { fetch, password } = {}) {
    const folderId = LINK_PATTERN.exec(url)[1];
    const token = await getGuestToken(fetch);
    const files = [];
    await listFolder(folderId, { fetch, token, password, subdir: '', files });
    if (files.length === 0) throw new ProviderError('No files found in this GoFile link', { provider: 'gofile' });
    return files;
  },
};
