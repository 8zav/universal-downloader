import { HttpError } from './errors.js';
import { DEFAULT_USER_AGENT } from './http.js';

const REQUEST_TIMEOUT_MS = 30_000;

export function createSession(fetch = globalThis.fetch, baseHeaders = {}) {
  const cookies = new Map();

  const cookieHeader = () => [...cookies].map(([name, value]) => `${name}=${value}`).join('; ');

  function remember(response) {
    for (const line of response.headers.getSetCookie?.() ?? []) {
      const [pair] = line.split(';');
      const separator = pair.indexOf('=');
      if (separator > 0) cookies.set(pair.slice(0, separator).trim(), pair.slice(separator + 1).trim());
    }
  }

  async function send(url, { method = 'GET', headers = {}, body } = {}) {
    const response = await fetch(url, {
      method,
      body,
      headers: {
        'User-Agent': DEFAULT_USER_AGENT,
        ...baseHeaders,
        ...headers,
        ...(cookies.size > 0 ? { Cookie: cookieHeader() } : {}),
      },
      redirect: 'follow',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    remember(response);
    if (!response.ok) throw new HttpError(response.status, response.statusText, url);
    return response;
  }

  return {
    send,
    cookieHeader,
    setCookie: (name, value) => cookies.set(name, value),
    text: async (url, options) => (await send(url, options)).text(),
    json: async (url, options) => (await send(url, options)).json(),
  };
}
