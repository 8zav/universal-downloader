import { fileNameFromUrl } from '../core/http.js';

export const direct = {
  name: 'direct',

  matches(url) {
    return /^https?:\/\//i.test(url);
  },

  async resolve(url) {
    return [{ id: url, url, filename: fileNameFromUrl(url) ?? undefined }];
  },
};
