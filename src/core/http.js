import { HttpError } from './errors.js';

export const DEFAULT_USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

const REQUEST_TIMEOUT_MS = 30_000;

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function request(url, { fetch = globalThis.fetch, headers = {}, method = 'GET', body } = {}) {
  const response = await fetch(url, {
    method,
    body,
    headers: { 'User-Agent': DEFAULT_USER_AGENT, ...headers },
    redirect: 'follow',
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    const retryAfter = Number(response.headers.get('retry-after'));
    throw new HttpError(response.status, response.statusText, url, Number.isFinite(retryAfter) ? retryAfter : null);
  }
  return response;
}

export async function getText(url, options) {
  return (await request(url, options)).text();
}

export async function getJson(url, options) {
  return (await request(url, options)).json();
}

export async function postJson(url, options) {
  return (await request(url, { ...options, method: 'POST' })).json();
}

export function parseContentDisposition(header) {
  if (!header) return null;
  const encoded = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

export function fileNameFromUrl(url) {
  try {
    const last = new URL(url).pathname.split('/').filter(Boolean).pop();
    return last ? decodeURIComponent(last) : null;
  } catch {
    return null;
  }
}
