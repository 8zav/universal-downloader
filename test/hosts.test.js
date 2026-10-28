import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ProviderError,
  dropbox,
  findProvider,
  googledrive,
  krakenfiles,
  mediafire,
  tmpfiles,
  wetransfer,
} from '../src/index.js';

const jsonResponse = (body, init = {}) =>
  new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' }, ...init });

function routedFetch(routes) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const route = routes.find(([pattern]) => pattern.test(String(url)));
    if (!route) throw new Error(`unexpected request: ${url}`);
    return route[1](String(url), init);
  };
  fetch.calls = calls;
  return fetch;
}

describe('routing', () => {
  const cases = [
    ['https://drive.google.com/file/d/1AbC_dEf/view?usp=sharing', 'googledrive'],
    ['https://www.dropbox.com/scl/fi/abc/file.zip?rlkey=x&dl=0', 'dropbox'],
    ['https://we.tl/t-abc123', 'wetransfer'],
    ['https://wetransfer.com/downloads/abc/def', 'wetransfer'],
    ['https://krakenfiles.com/view/AbC123/file.html', 'krakenfiles'],
    ['https://tmpfiles.org/12345/file.zip', 'tmpfiles'],
    ['https://www.mediafire.com/folder/abcd1234/Stuff', 'mediafire'],
    ['https://files.catbox.moe/abc123.zip', 'direct'],
    ['https://litter.catbox.moe/abc123.zip', 'direct'],
    ['https://a.uguu.se/abc.zip', 'direct'],
    ['https://0x0.st/abcd.bin', 'direct'],
    ['https://transfer.sh/abc/file.zip', 'direct'],
  ];
  for (const [url, expected] of cases) {
    it(`${url} -> ${expected}`, () => assert.equal(findProvider(url).name, expected));
  }
});

describe('dropbox', () => {
  it('forces dl=1 and drops raw', async () => {
    const [source] = await dropbox.resolve('https://www.dropbox.com/scl/fi/abc/my%20file.zip?rlkey=k&dl=0&raw=1');
    const url = new URL(source.url);
    assert.equal(url.searchParams.get('dl'), '1');
    assert.equal(url.searchParams.has('raw'), false);
    assert.equal(url.searchParams.get('rlkey'), 'k');
    assert.equal(source.filename, 'my file.zip');
  });

  it('leaves the name to the server for folders', async () => {
    const [source] = await dropbox.resolve('https://www.dropbox.com/scl/fo/abc/xyz?rlkey=k');
    assert.equal(source.filename, undefined);
  });
});

describe('tmpfiles', () => {
  it('rewrites the landing page to the /dl/ URL', async () => {
    const [source] = await tmpfiles.resolve('https://tmpfiles.org/12345/file.zip');
    assert.equal(source.url, 'https://tmpfiles.org/dl/12345/file.zip');
    assert.equal(source.filename, 'file.zip');
  });

  it('keeps an already direct URL', async () => {
    const [source] = await tmpfiles.resolve('https://tmpfiles.org/dl/12345/file.zip');
    assert.equal(source.url, 'https://tmpfiles.org/dl/12345/file.zip');
  });
});

describe('googledrive', () => {
  const link = 'https://drive.google.com/file/d/1AbC_dEf/view';
  const zipResponse = () => new Response('data', {
    headers: { 'content-type': 'application/zip', 'content-length': '4', 'content-disposition': 'attachment; filename="big.zip"' },
  });

  it('uses the direct download when Drive serves the file right away', async () => {
    const fetch = routedFetch([[/drive\.usercontent\.google\.com/, zipResponse]]);

    const [source] = await googledrive.resolve(link, { fetch });

    assert.equal(source.filename, 'big.zip');
    assert.equal(source.size, 4);
    assert.match(source.url, /id=1AbC_dEf/);
  });

  it('follows the virus-scan confirmation form', async () => {
    const html = `<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
      <input type="hidden" name="id" value="1AbC_dEf"><input type="hidden" name="confirm" value="t">
      <input type="hidden" name="uuid" value="u-123"></form>`;
    const fetch = routedFetch([
      [/uuid=/, zipResponse],
      [/drive\.usercontent\.google\.com/, () => new Response(html, { headers: { 'content-type': 'text/html' } })],
    ]);

    const [source] = await googledrive.resolve(link, { fetch });

    assert.match(source.url, /uuid=u-123/);
    assert.equal(source.filename, 'big.zip');
  });

  it('explains folders are unsupported', async () => {
    await assert.rejects(googledrive.resolve('https://drive.google.com/drive/folders/abc'), ProviderError);
  });

  it('fails clearly when the file is private', async () => {
    const fetch = routedFetch([[/./, () => new Response('<html>Sign in</html>', { headers: { 'content-type': 'text/html' } })]]);
    await assert.rejects(googledrive.resolve(link, { fetch }), ProviderError);
  });
});

describe('wetransfer', () => {
  it('follows we.tl, fetches the CSRF token and posts the security hash', async () => {
    const final = 'https://wetransfer.com/downloads/tid123/hash456';
    const fetch = routedFetch([
      [/we\.tl/, () => Object.defineProperty(new Response(null), 'url', { value: final })],
      [/api\/v4\/transfers\/tid123\/download/, () => jsonResponse({ direct_link: 'https://cdn.example/file.zip' })],
      [/wetransfer\.com\/$/, () => {
        const headers = new Headers({ 'content-type': 'text/html' });
        headers.append('set-cookie', '_wt_session=abc; Path=/; HttpOnly');
        return new Response('<meta name="csrf-token" content="TOKEN42">', { headers });
      }],
    ]);

    const [source] = await wetransfer.resolve('https://we.tl/t-abc', { fetch });

    assert.equal(source.url, 'https://cdn.example/file.zip');
    const post = fetch.calls.find((call) => call.url.includes('/download'));
    assert.equal(post.init.method, 'POST');
    assert.equal(post.init.headers['x-csrf-token'], 'TOKEN42');
    assert.equal(post.init.headers.Cookie, '_wt_session=abc');
    assert.deepEqual(JSON.parse(post.init.body), { intent: 'entire_transfer', security_hash: 'hash456' });
  });

  it('includes the recipient id when present', async () => {
    const fetch = routedFetch([
      [/wetransfer\.com\/$/, () => new Response('')],
      [/download/, () => jsonResponse({ direct_link: 'https://cdn.example/x' })],
    ]);
    await wetransfer.resolve('https://wetransfer.com/downloads/t/r/h', { fetch });
    const body = JSON.parse(fetch.calls.at(-1).init.body);
    assert.equal(body.recipient_id, 'r');
  });

  it('reports expired transfers', async () => {
    const fetch = routedFetch([
      [/wetransfer\.com\/$/, () => new Response('')],
      [/download/, () => jsonResponse({ message: 'expired' })],
    ]);
    await assert.rejects(wetransfer.resolve('https://wetransfer.com/downloads/t/h', { fetch }), /expired/);
  });
});

describe('krakenfiles', () => {
  it('posts the page token and returns the signed URL', async () => {
    const page = '<input type="hidden" id="dl-token" value="TOK"><div data-file-hash="HASH1"></div>';
    const fetch = routedFetch([
      [/\/view\//, () => new Response(page)],
      [/\/download\/HASH1/, () => jsonResponse({ url: 'https://s1.krakenfiles.com/force-download/abc/file.zip' })],
    ]);

    const [source] = await krakenfiles.resolve('https://krakenfiles.com/view/AbC/file.html', { fetch });

    assert.equal(source.url, 'https://s1.krakenfiles.com/force-download/abc/file.zip');
    const post = fetch.calls.find((call) => call.init.method === 'POST');
    assert.equal(post.init.headers.hash, 'HASH1');
    assert.equal(post.init.body.get('token'), 'TOK');
  });

  it('fails clearly when the page has no token', async () => {
    const fetch = routedFetch([[/./, () => new Response('<html></html>')]]);
    await assert.rejects(krakenfiles.resolve('https://krakenfiles.com/view/AbC/file.html', { fetch }), ProviderError);
  });
});

describe('mediafire folders', () => {
  const page = (name) => () => new Response(`<a id="downloadButton" href="https://download1.mediafire.com/x/${name}">Download</a>`);
  const content = (extra) => jsonResponse({ response: { result: 'Success', folder_content: { more_chunks: 'no', ...extra } } });

  function folderApi(url) {
    const params = new URL(url).searchParams;
    const key = params.get('folder_key');
    const type = params.get('content_type');
    if (key === 'root' && type === 'files') {
      return content({ files: [{ filename: 'a.zip', size: '5', links: { normal_download: 'https://www.mediafire.com/file/a/a.zip/file' } }] });
    }
    if (key === 'root' && type === 'folders') return content({ folders: [{ folderkey: 'sub', name: 'Inner' }] });
    if (key === 'sub' && type === 'files') {
      return content({ files: [{ filename: 'b.zip', size: '7', links: { normal_download: 'https://www.mediafire.com/file/b/b.zip/file' } }] });
    }
    return content({ folders: [] });
  }

  it('lists files and sub-folders through the public API and resolves each page', async () => {
    const fetch = routedFetch([
      [/folder\/get_content/, folderApi],
      [/file\/a\//, page('a.zip')],
      [/file\/b\//, page('b.zip')],
    ]);

    const sources = await mediafire.resolve('https://www.mediafire.com/folder/root/Stuff', { fetch });

    assert.deepEqual(sources.map((source) => [source.filename, source.subdir]), [['a.zip', undefined], ['b.zip', 'Inner']]);
    assert.equal(sources[1].url, 'https://download1.mediafire.com/x/b.zip');
    assert.equal(sources[1].size, 7);
  });

  it('surfaces API errors as ProviderError', async () => {
    const fetch = routedFetch([[/get_content/, () => jsonResponse({ response: { result: 'Error', message: 'Unknown or invalid FolderKey' } })]]);
    await assert.rejects(mediafire.resolve('https://www.mediafire.com/folder/bad/x', { fetch }), /FolderKey/);
  });
});
