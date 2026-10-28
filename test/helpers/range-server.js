import { createServer } from 'node:http';

const CHUNK_SIZE = 16 * 1024;

export async function startRangeServer(content, { delayMs = 0, honorRange = true } = {}) {
  const requests = [];
  const state = { active: 0, maxActive: 0 };

  const server = createServer(async (req, res) => {
    requests.push({ url: req.url, range: req.headers.range ?? null });
    state.active += 1;
    state.maxActive = Math.max(state.maxActive, state.active);
    res.on('close', () => { state.active -= 1; });

    const match = honorRange ? /bytes=(\d+)-/.exec(req.headers.range ?? '') : null;
    const start = match ? Number(match[1]) : 0;
    if (start >= content.length) {
      res.writeHead(416, { 'Content-Range': `bytes */${content.length}` }).end();
      return;
    }

    const body = content.subarray(start);
    res.writeHead(match ? 206 : 200, {
      'Content-Length': body.length,
      'Content-Type': 'application/octet-stream',
      ...(match ? { 'Content-Range': `bytes ${start}-${content.length - 1}/${content.length}` } : {}),
    });

    for (let offset = 0; offset < body.length && !res.destroyed; offset += CHUNK_SIZE) {
      res.write(body.subarray(offset, offset + CHUNK_SIZE));
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    res.end();
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return {
    url: (name = 'file.bin') => `http://127.0.0.1:${port}/${name}`,
    requests,
    state,
    close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }),
  };
}
