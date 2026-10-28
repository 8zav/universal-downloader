import { ProviderError } from '../core/errors.js';
import { findTag, readAttribute } from '../core/html.js';
import { fileNameFromUrl } from '../core/http.js';
import { createSession } from '../core/session.js';

const LINK_PATTERN = /^https?:\/\/(?:www\.)?krakenfiles\.com\/view\/([\w-]+)\//i;
const BASE = 'https://krakenfiles.com';

export const krakenfiles = {
  name: 'krakenfiles',

  matches(url) {
    return LINK_PATTERN.test(url);
  },

  async resolve(url, { fetch } = {}) {
    const session = createSession(fetch);
    const html = await session.text(url);

    const token = readAttribute(findTag(html, /<input\b[^>]*id=["']dl-token["'][^>]*>/i) ?? '', 'value');
    const hash = /data-file-hash=["']([^"']+)["']/i.exec(html)?.[1];
    if (!token || !hash) {
      throw new ProviderError('Token or file hash not found (file removed or page layout changed)', { provider: 'krakenfiles' });
    }

    const form = new FormData();
    form.append('token', token);
    const result = await session.json(`${BASE}/download/${hash}`, { method: 'POST', headers: { hash }, body: form });
    if (!result?.url) throw new ProviderError('The host refused to give a download URL', { provider: 'krakenfiles' });

    const cookie = session.cookieHeader();
    return [{
      id: hash,
      url: result.url,
      filename: fileNameFromUrl(result.url) ?? undefined,
      headers: { Referer: url, ...(cookie ? { Cookie: cookie } : {}) },
    }];
  },
};
