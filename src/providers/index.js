import { ProviderError } from '../core/errors.js';
import { direct } from './direct.js';
import { dropbox } from './dropbox.js';
import { gofile } from './gofile.js';
import { googledrive } from './googledrive.js';
import { krakenfiles } from './krakenfiles.js';
import { mediafire } from './mediafire.js';
import { swisstransfer } from './swisstransfer.js';
import { tmpfiles } from './tmpfiles.js';
import { wetransfer } from './wetransfer.js';

export const builtinProviders = Object.freeze([
  gofile,
  swisstransfer,
  mediafire,
  googledrive,
  dropbox,
  wetransfer,
  krakenfiles,
  tmpfiles,
  direct,
]);

export function findProvider(url, extraProviders = []) {
  const provider = [...extraProviders, ...builtinProviders].find((candidate) => candidate.matches(url));
  if (!provider) throw new ProviderError(`Unsupported URL: ${url}`);
  return provider;
}

export async function resolveSources(url, { providers = [], ...context } = {}) {
  const provider = findProvider(url, providers);
  const sources = await provider.resolve(url, context);
  return { provider, sources };
}

export { direct, dropbox, gofile, googledrive, krakenfiles, mediafire, swisstransfer, tmpfiles, wetransfer };
