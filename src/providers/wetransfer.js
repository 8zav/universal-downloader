import { ProviderError } from '../core/errors.js';
import { createSession } from '../core/session.js';

const HOST_PATTERN = /^https?:\/\/(?:we\.tl|(?:www\.)?wetransfer\.com)\//i;
const API = 'https://wetransfer.com/api/v4/transfers';
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:102.0) Gecko/20100101 Firefox/102.0';

function parseDownloadPath(url) {
  const parts = new URL(url).pathname.split('/').filter(Boolean);
  const rest = parts[0] === 'downloads' ? parts.slice(1) : parts;
  if (rest.length === 2) return { transferId: rest[0], securityHash: rest[1] };
  if (rest.length === 3) return { transferId: rest[0], recipientId: rest[1], securityHash: rest[2] };
  return null;
}

export const wetransfer = {
  name: 'wetransfer',

  matches(url) {
    return HOST_PATTERN.test(url);
  },

  async resolve(url, { fetch } = {}) {
    const session = createSession(fetch, { 'User-Agent': USER_AGENT, 'x-requested-with': 'XMLHttpRequest' });

    let target = url;
    if (/^https?:\/\/we\.tl\//i.test(url)) target = (await session.send(url, { method: 'HEAD' })).url;

    const ids = parseDownloadPath(target);
    if (!ids) throw new ProviderError('Unrecognized WeTransfer link', { provider: 'wetransfer' });

    const home = await session.text('https://wetransfer.com/');
    const csrf = /name="csrf-token" content="([^"]+)"/.exec(home)?.[1];

    const body = { intent: 'entire_transfer', security_hash: ids.securityHash };
    if (ids.recipientId) body.recipient_id = ids.recipientId;

    const result = await session.json(`${API}/${ids.transferId}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'x-csrf-token': csrf } : {}) },
      body: JSON.stringify(body),
    });
    if (!result?.direct_link) {
      throw new ProviderError(result?.message ?? 'No download link (transfer expired or deleted?)', { provider: 'wetransfer' });
    }
    return [{ id: ids.transferId, url: result.direct_link, headers: { 'User-Agent': USER_AGENT } }];
  },
};
