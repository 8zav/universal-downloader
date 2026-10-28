import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { CancelledError, Download, DownloadManager } from '../src/index.js';
import { startRangeServer } from './helpers/range-server.js';

const PAYLOAD = randomBytes(512 * 1024);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(condition, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('waitFor timed out');
    await sleep(10);
  }
}

describe('Download', () => {
  let dir;
  let server;

  beforeEach(async () => {
    dir = await mkdtemp(path.join(tmpdir(), 'udl-test-'));
  });

  afterEach(async () => {
    await server?.close();
    await rm(dir, { recursive: true, force: true });
  });

  it('downloads a file and reports completed progress', async () => {
    server = await startRangeServer(PAYLOAD);
    const download = new Download({ url: server.url('a.bin'), filename: 'a.bin' }, { dir, progressIntervalMs: 0 });

    const saved = await download.start();

    assert.equal(saved, path.join(dir, 'a.bin'));
    assert.deepEqual(await readFile(saved), PAYLOAD);
    assert.equal(download.progress.state, 'completed');
    assert.equal(download.progress.percent, 100);
    assert.equal(download.progress.remaining, 0);
    assert.equal(download.progress.eta, 0);
    assert.equal(download.progress.etaText, '0s');
  });

  it('already knows the speed and ETA at the first progress event, even for a fast download', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 20 });
    const download = new Download({ url: server.url(), filename: 'e.bin' }, { dir, progressIntervalMs: 0 });
    const first = new Promise((resolve) => download.once('progress', resolve));

    const finished = download.start();
    const snapshot = await first;
    await finished;

    assert.ok(snapshot.speed > 0, 'speed available on the first chunk');
    assert.ok(snapshot.eta > 0 && snapshot.etaText !== '?');
  });

  it('reports speed, remaining bytes and ETA while downloading', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 20 });
    const download = new Download({ url: server.url(), filename: 'p.bin' }, { dir, progressIntervalMs: 0 });
    const snapshots = [];
    download.on('progress', (snapshot) => snapshots.push(snapshot));

    await download.start();

    const midway = snapshots.filter((snapshot) => snapshot.speed > 0 && snapshot.state === 'downloading');
    assert.ok(midway.length > 0, 'expected snapshots with a measured speed');
    assert.ok(midway.every((snapshot) => snapshot.total === PAYLOAD.length));
    assert.ok(midway.some((snapshot) => snapshot.eta > 0 && snapshot.remaining > 0));
  });

  it('pauses, then resumes with a Range request and produces an identical file', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 25 });
    const download = new Download({ url: server.url(), filename: 'r.bin' }, { dir, progressIntervalMs: 0 });
    const finished = download.start();

    await waitFor(() => download.progress.downloaded > 64 * 1024);
    assert.equal(download.pause(), true);
    assert.equal(download.state, 'paused');
    const partSize = (await stat(path.join(dir, 'r.bin.part'))).size;
    assert.ok(partSize > 0 && partSize < PAYLOAD.length);

    assert.equal(await download.resume(), true);
    const saved = await finished;

    assert.deepEqual(await readFile(saved), PAYLOAD);
    assert.ok(server.requests.some((request) => /^bytes=\d+-$/.test(request.range ?? '')), 'resume must send Range');
  });

  it('restarts from zero when the server ignores Range', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 25, honorRange: false });
    const download = new Download({ url: server.url(), filename: 'n.bin' }, { dir, progressIntervalMs: 0 });
    const finished = download.start();

    await waitFor(() => download.progress.downloaded > 64 * 1024);
    download.pause();
    await download.resume();

    assert.deepEqual(await readFile(await finished), PAYLOAD);
  });

  it('cancels, deletes the partial file and rejects wait()', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 25 });
    const download = new Download({ url: server.url(), filename: 'c.bin' }, { dir, progressIntervalMs: 0 });
    const finished = download.start();

    await waitFor(() => download.progress.downloaded > 32 * 1024);
    await download.cancel();

    await assert.rejects(finished, CancelledError);
    assert.equal(download.state, 'cancelled');
    await assert.rejects(stat(path.join(dir, 'c.bin.part')));
  });

  it('fails without retrying on a 404 and exposes the error', async () => {
    const notFound = new Download(
      { url: 'http://127.0.0.1:1/x.bin', filename: 'x.bin' },
      { dir, retries: 0, fetch: async () => new Response('nope', { status: 404 }) },
    );

    await assert.rejects(notFound.start(), { name: 'HttpError', status: 404 });
    assert.equal(notFound.state, 'failed');
  });

  it('refuses to save a web page as if it were the file', async () => {
    const webPage = async () => new Response('<!DOCTYPE html><title>Home</title>', { headers: { 'content-type': 'text/html; charset=utf-8' } });
    const download = new Download({ url: 'http://127.0.0.1:1/dl/abc', filename: 'game.zip' }, { dir, fetch: webPage });

    await assert.rejects(download.start(), { name: 'ProviderError', message: /web page/ });
    await assert.rejects(stat(path.join(dir, 'game.zip')));
  });

  it('follows a two-step signed URL, keeps host cookies away from storage, and re-signs on resume', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 25 });
    const linkId = '01a18222-c204-7186-9a3f-790467825b55';
    const storageRequests = [];
    let signCount = 0;
    const page = JSON.stringify({ component: 'link/show', props: { transfer: { files: [{ id: 'f1', path: 'pack.bin', size: PAYLOAD.length }] } } });
    const fakeFetch = async (url, init = {}) => {
      const target = String(url);
      if (target === `https://www.swisstransfer.com/dl/${linkId}`) {
        const headers = new Headers({ 'content-type': 'text/html' });
        headers.append('set-cookie', 'ST_SESSION=secret; Path=/');
        return new Response(`<script data-page="app" type="application/json">${page}</script>`, { headers });
      }
      if (target === `https://www.swisstransfer.com/api/1/links/${linkId}/files/f1`) {
        signCount += 1;
        assert.match(init.headers.Cookie, /ST_SESSION=secret/, 'the API call must carry the session cookie');
        return new Response(JSON.stringify({ result: 'success', data: { url: server.url('signed.bin') } }), { headers: { 'content-type': 'application/json' } });
      }
      storageRequests.push(init.headers);
      return fetch(url, init);
    };
    const manager = new DownloadManager({ dir, fetch: fakeFetch, progressIntervalMs: 0 });

    const [download] = await manager.add(`https://www.swisstransfer.com/d/${linkId}`);
    await waitFor(() => download.progress.downloaded > 64 * 1024);
    download.pause();
    await download.resume();
    const saved = await download.wait();

    assert.deepEqual(await readFile(saved), PAYLOAD);
    assert.equal(signCount, 2, 'a fresh signed URL is requested on resume');
    for (const headers of storageRequests) {
      assert.equal(headers.Cookie, undefined);
      assert.equal(headers.Referer, undefined);
    }
  });

  it('rejects an API JSON answer saved in place of the file', async () => {
    const apiAnswer = async () => new Response('{"result":"success","data":{"url":"x"}}', { headers: { 'content-type': 'application/json' } });
    const download = new Download({ url: 'http://127.0.0.1:1/api', filename: 'game.lua' }, { dir, fetch: apiAnswer });

    await assert.rejects(download.start(), { name: 'ProviderError' });
    await assert.rejects(stat(path.join(dir, 'game.lua.part')));
  });

  it('never writes outside the download dir', async () => {
    server = await startRangeServer(PAYLOAD);
    const download = new Download({ url: server.url(), filename: '../../evil.bin', subdir: '../..' }, { dir });

    const saved = await download.start();

    assert.ok(path.resolve(saved).startsWith(path.resolve(dir)));
  });
});

describe('DownloadManager', () => {
  let dir;
  let server;

  beforeEach(async () => {
    dir = await mkdtemp(path.join(tmpdir(), 'udl-test-'));
  });

  afterEach(async () => {
    await server?.close();
    await rm(dir, { recursive: true, force: true });
  });

  it('respects the concurrency limit and finishes every file', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 5 });
    const manager = new DownloadManager({ dir, concurrency: 2 });
    let maxRunning = 0;
    manager.on('state', () => {
      const running = manager.downloads.filter((item) => item.state === 'downloading').length;
      maxRunning = Math.max(maxRunning, running);
    });

    const files = await Promise.all([1, 2, 3, 4, 5].map((n) => manager.add(server.url(`f${n}.bin`))));
    await manager.onIdle();

    assert.equal(files.length, 5);
    assert.equal(maxRunning, 2);
    assert.equal(manager.stats().completed, 5);
    for (const [download] of files) assert.deepEqual(await readFile(download.filePath), PAYLOAD);
  });

  it('frees the slot of a paused download for the next queued one', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 20 });
    const manager = new DownloadManager({ dir, concurrency: 1 });
    const [first] = await manager.add(server.url('one.bin'));
    const [second] = await manager.add(server.url('two.bin'));

    await waitFor(() => first.progress.downloaded > 32 * 1024);
    assert.equal(second.state, 'queued');
    manager.pause(first);
    await waitFor(() => second.state === 'downloading');

    manager.resume(first);
    await manager.onIdle();
    await first.wait();
    await second.wait();

    assert.deepEqual(await readFile(first.filePath), PAYLOAD);
  });

  it('aggregates speed and remaining bytes across downloads', async () => {
    server = await startRangeServer(PAYLOAD, { delayMs: 20 });
    const manager = new DownloadManager({ dir, concurrency: 2, progressIntervalMs: 0 });
    await manager.add(server.url('s1.bin'));
    await manager.add(server.url('s2.bin'));

    await waitFor(() => manager.stats().speed > 0);
    const stats = manager.stats();

    assert.equal(stats.active, 2);
    assert.ok(stats.remaining > 0 && stats.remaining < PAYLOAD.length * 2);
    assert.ok(stats.eta > 0);
    await manager.cancelAll();
  });
});
