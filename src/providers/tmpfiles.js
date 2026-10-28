import { fileNameFromUrl } from '../core/http.js';

const LINK_PATTERN = /^https?:\/\/(?:www\.)?tmpfiles\.org\/(?!dl\/)(\d+)\/(.+)$/i;
const DIRECT_PATTERN = /^https?:\/\/(?:www\.)?tmpfiles\.org\/dl\//i;

export const tmpfiles = {
  name: 'tmpfiles',

  matches(url) {
    return LINK_PATTERN.test(url) || DIRECT_PATTERN.test(url);
  },

  async resolve(url) {
    const match = LINK_PATTERN.exec(url);
    const direct = match ? `https://tmpfiles.org/dl/${match[1]}/${match[2]}` : url;
    return [{ id: direct, url: direct, filename: fileNameFromUrl(direct) ?? undefined }];
  },
};
