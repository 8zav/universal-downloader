#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { DownloadManager, formatBytes, formatSpeed } from '../src/index.js';

const USAGE = `Usage: udl <url...> [options]

  -o, --out <dir>          destination folder (default: ./downloads)
  -c, --concurrency <n>    parallel downloads (default: 3)
      --password <text>    password for protected links
  -h, --help               show this help

While downloading: [p] pause all  [r] resume all  [q] quit (partial files are kept)`;

const REFRESH_MS = 500;
const BAR_WIDTH = 20;

function bar(percent) {
  const filled = Math.round(((percent ?? 0) / 100) * BAR_WIDTH);
  return `[${'#'.repeat(filled)}${'.'.repeat(BAR_WIDTH - filled)}]`;
}

function line(snapshot) {
  const size = snapshot.total === null ? formatBytes(snapshot.downloaded) : `${formatBytes(snapshot.downloaded)}/${formatBytes(snapshot.total)}`;
  const percent = snapshot.percent === null ? '  ?  ' : `${snapshot.percent.toFixed(1).padStart(5)}%`;
  const left = snapshot.remaining === null ? '?' : formatBytes(snapshot.remaining);
  return `${snapshot.fileName.slice(0, 30).padEnd(30)} ${bar(snapshot.percent)} ${percent} ${size}  ${formatSpeed(snapshot.speed)}  left ${left}  ETA ${snapshot.etaText}  ${snapshot.state}`;
}

function render(manager, previousLines) {
  const lines = manager.downloads.map((item) => line(item.progress));
  const stats = manager.stats();
  lines.push(`Total: ${stats.active} active, ${stats.queued} queued, ${stats.paused} paused, ${stats.completed} done, ${stats.failed} failed | ${formatSpeed(stats.speed)} | left ${formatBytes(stats.remaining)} | ETA ${stats.etaText}`);

  const up = previousLines > 0 ? `\x1b[${previousLines}A` : '';
  process.stdout.write(`${up}${lines.map((text) => `\x1b[2K${text}`).join('\n')}\n`);
  return lines.length;
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      out: { type: 'string', short: 'o', default: 'downloads' },
      concurrency: { type: 'string', short: 'c', default: '3' },
      password: { type: 'string' },
      help: { type: 'boolean', short: 'h' },
    },
  });
  if (values.help || positionals.length === 0) {
    process.stdout.write(`${USAGE}\n`);
    return;
  }

  const manager = new DownloadManager({ dir: values.out, concurrency: Number(values.concurrency) || 3, password: values.password });
  manager.on('failed', (item, error) => process.stderr.write(`\n${item.fileName}: ${error.message}\n`));

  for (const url of positionals) {
    try {
      await manager.add(url);
    } catch (error) {
      process.stderr.write(`${url}: ${error.message}\n`);
    }
  }

  let drawn = 0;
  const timer = setInterval(() => { drawn = render(manager, drawn); }, REFRESH_MS);
  const finish = (code) => {
    clearInterval(timer);
    render(manager, drawn);
    process.exit(code);
  };

  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.on('data', (key) => {
      const pressed = String(key);
      if (pressed === 'p') manager.pauseAll();
      else if (pressed === 'r') manager.resumeAll();
      else if (pressed === 'q' || pressed === '\u0003') { manager.pauseAll(); finish(130); }
    });
  }
  process.on('SIGINT', () => { manager.pauseAll(); finish(130); });

  await manager.onIdle();
  finish(manager.stats().failed > 0 ? 1 : 0);
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
});
