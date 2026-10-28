import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { formatBytes, formatDuration, findProvider, ProviderError } from '../src/index.js';
import { buildWebsiteToken } from '../src/providers/gofile.js';
import { extractDownloadLink } from '../src/providers/mediafire.js';
import { swisstransfer } from '../src/providers/swisstransfer.js';
import { mediafire } from '../src/providers/mediafire.js';

const json = (body) => async () => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });

describe('provider matching', () => {
  it('routes each link to the right provider', () => {
    assert.equal(findProvider('https://gofile.io/d/AbC123').name, 'gofile');
    assert.equal(findProvider('https://www.swisstransfer.com/d/0a1b2c3d-0a1b-0a1b-0a1b-0a1b2c3d4e5f').name, 'swisstransfer');
    assert.equal(findProvider('https://www.mediafire.com/file/abc/name.zip/file').name, 'mediafire');
    assert.equal(findProvider('https://cdn.discordapp.com/attachments/1/2/x.zip').name, 'direct');
  });

  it('rejects non-http URLs', () => {
    assert.throws(() => findProvider('ftp://example.com/a'), ProviderError);
  });
});

describe('swisstransfer', () => {
  const id = '01a18222-c204-7186-9a3f-790467825b55';
  const page = (props, component = 'link/show') => async () =>
    new Response(JSON.stringify({ component, props }), { headers: { 'content-type': 'application/json' } });

  it('maps the Inertia page files to the new /api/1/links download URLs', async () => {
    const fetch = page({ transfer: { files: [{ id: 'f1', path: 'Docs/Sub/a.zip', size: 42 }, { id: 'f2', path: 'b.txt', size: 1 }] } });

    const sources = await swisstransfer.resolve(`https://www.swisstransfer.com/dl/${id}`, { fetch });

    assert.equal(typeof sources[0].resolveUrl, 'function');
    assert.equal(sources[0].url, `https://www.swisstransfer.com/api/1/links/${id}/files/f1`);
    assert.equal(sources[0].filename, 'a.zip');
    assert.equal(sources[0].subdir, 'Docs/Sub');
    assert.equal(sources[0].size, 42);
    assert.equal(sources[1].subdir, undefined);
  });

  it('accepts the old /d/ link form and asks for the plain page (no X-Inertia, it answers 409)', async () => {
    const seen = [];
    const fetch = async (url, init) => {
      seen.push({ url: String(url), headers: init.headers });
      return page({ files: [{ id: 'x', name: 'n.bin', size: 3 }] })();
    };

    await swisstransfer.resolve(`https://www.swisstransfer.com/d/${id}`, { fetch });

    assert.equal(seen[0].url, `https://www.swisstransfer.com/dl/${id}`);
    assert.equal(seen[0].headers['X-Inertia'], undefined);
  });

  it('reads the page JSON from <script data-page="app"> (current site) and ignores the "app" label', async () => {
    const data = JSON.stringify({ component: 'link/show', props: { transfer: { files: [{ id: 'f7', path: 'Game/pack.zip', size: 9 }] } } });
    const html = `<html><body><script data-page="app" type="application/json">${data}</script><div id="app"></div></body></html>`;
    const fetch = async () => new Response(html, { headers: { 'content-type': 'text/html' } });

    const [source] = await swisstransfer.resolve(`https://www.swisstransfer.com/d/${id}`, { fetch });

    assert.equal(source.filename, 'pack.zip');
    assert.equal(source.subdir, 'Game');
  });

  it('gives a ProviderError (not a JSON crash) when the page has no data', async () => {
    const fetch = async () => new Response('<html><body>nothing</body></html>', { headers: { 'content-type': 'text/html' } });
    await assert.rejects(swisstransfer.resolve(`https://www.swisstransfer.com/d/${id}`, { fetch }), ProviderError);
  });

  it('falls back to the page embedded in the data-page attribute', async () => {
    const embedded = JSON.stringify({ component: 'link/show', props: { transfer: { files: [{ id: 'f9', path: 'z.zip', size: 5 }] } } })
      .replaceAll('"', '&quot;');
    const fetch = async () => new Response(`<div id="app" data-page="${embedded}"></div>`, { headers: { 'content-type': 'text/html' } });

    const [source] = await swisstransfer.resolve(`https://www.swisstransfer.com/dl/${id}`, { fetch });

    assert.equal(source.filename, 'z.zip');
  });

  it('reports password protected transfers', async () => {
    const fetch = page({}, 'link/password');
    await assert.rejects(swisstransfer.resolve(`https://www.swisstransfer.com/dl/${id}`, { fetch }), /password/);
  });

  it('turns a 404 into a ProviderError', async () => {
    const fetch = async () => new Response('{}', { status: 404 });
    await assert.rejects(swisstransfer.resolve(`https://www.swisstransfer.com/dl/${id}`, { fetch }), ProviderError);
  });
});

describe('mediafire', () => {
  const real = 'https://download1234.mediafire.com/abc/file.zip';

  it('reads the scrambled (base64) download link', () => {
    const html = `<a class="input popsok" id="downloadButton" href="#" data-scrambled-url="${Buffer.from(real).toString('base64')}">Download</a>`;
    assert.equal(extractDownloadLink(html), real);
  });

  it('falls back to the plain href', () => {
    const html = `<a class="input popsok" aria-label="Download" href="${real}?a=1&amp;b=2" id="downloadButton">Download</a>`;
    assert.equal(extractDownloadLink(html), `${real}?a=1&b=2`);
  });

  it('returns null when the page has no button', () => {
    assert.equal(extractDownloadLink('<html></html>'), null);
  });

  it('resolves a page through fetch', async () => {
    const html = `<a id="downloadButton" href="${real}">Download</a>`;
    const fetch = async () => new Response(html);
    const [source] = await mediafire.resolve('https://www.mediafire.com/file/x/file.zip/file', { fetch });
    assert.equal(source.url, real);
    assert.equal(source.filename, 'file.zip');
  });
});

describe('gofile', () => {
  it('builds a stable website token for the same 4h slot', () => {
    const now = Date.UTC(2026, 0, 1, 12, 0, 0);
    assert.equal(buildWebsiteToken('tok', 'UA', now), buildWebsiteToken('tok', 'UA', now + 60_000));
    assert.notEqual(buildWebsiteToken('tok', 'UA', now), buildWebsiteToken('tok', 'UA', now + 5 * 3600_000));
    assert.match(buildWebsiteToken('tok', 'UA', now), /^[0-9a-f]{64}$/);
  });
});

describe('formatters', () => {
  it('formats bytes', () => {
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(1536), '1.50 KB');
    assert.equal(formatBytes(5 * 1024 ** 3), '5.00 GB');
    assert.equal(formatBytes(null), '?');
  });

  it('formats durations in seconds, minutes, hours and days', () => {
    assert.equal(formatDuration(45), '45s');
    assert.equal(formatDuration(200), '3m 20s');
    assert.equal(formatDuration(3900), '1h 5m');
    assert.equal(formatDuration(2 * 86400 + 3 * 3600), '2d 3h');
    assert.equal(formatDuration(Infinity), '?');
  });
});
