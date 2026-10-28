import { fileNameFromUrl } from '../core/http.js';

const LINK_PATTERN = /^https?:\/\/(?:www\.)?dropbox\.com\/(?:s|scl|sh)\//i;

export const dropbox = {
  name: 'dropbox',

  matches(url) {
    return LINK_PATTERN.test(url);
  },

  async resolve(url) {
    const link = new URL(url);
    link.searchParams.delete('raw');
    link.searchParams.set('dl', '1');
    const isFolder = link.pathname.startsWith('/scl/fo/') || link.pathname.startsWith('/sh/');
    const filename = isFolder ? undefined : fileNameFromUrl(link.href) ?? undefined;
    return [{ id: link.href, url: link.href, filename }];
  },
};
