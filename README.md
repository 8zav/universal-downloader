# universal-downloader

Download files from file-sharing services with one API: **pause / resume**, **live speed**, **bytes left**, **ETA** and **concurrent downloads**. Zero dependencies, Node.js 18.17+.

Also available for **Python 3.9+** (standard library only): see [python/](python/README.md). Both versions share the same design and the same providers.

## Supported services

None of these need an API key.

| Service | Notes |
|---|---|
| Direct links: catbox / litterbox, uguu, 0x0.st, transfer.sh, GitHub releases, Discord and other CDNs | works through the `direct` provider |
| MediaFire | files and folders (sub-folders included) |
| GoFile | folders and sub-folders, password supported; token scheme still being verified |
| SwissTransfer | all files of a transfer |
| Google Drive | public files, including large files with the virus-scan page; folders are not supported (they need an API key) |
| Dropbox | shared files and folders (folders arrive as a zip) |
| WeTransfer | `wetransfer.com` and `we.tl` links (the whole transfer, usually a zip) |
| Krakenfiles | single files |
| tmpfiles.org | single files |

All providers are tested against simulated responses; live checks depend on each host's current API, which can change without notice.
Planned: Mega (needs client-side decryption), OneDrive, 1fichier, Bunkr. Adding a host is a single small file, see [Writing a provider](#writing-a-provider).

## Install

```bash
npm install universal-downloader
```

## Usage

```js
import { DownloadManager, formatBytes, formatSpeed } from 'universal-downloader';

const manager = new DownloadManager({ dir: './downloads', concurrency: 3 });

manager.on('progress', (download, p) => {
  console.log(
    `${p.fileName} ${p.percent?.toFixed(1)}% | ${formatSpeed(p.speed)} | ` +
    `left ${formatBytes(p.remaining)} | ETA ${p.etaText}`
  );
});

const [file] = await manager.add('https://www.mediafire.com/file/...');

file.pause();          // keeps the .part file
await file.resume();   // continues with an HTTP Range request
await file.cancel();   // stops and deletes the partial file

await manager.onIdle();
console.log(manager.stats()); // total speed, bytes left and ETA for the whole queue
```

A share link can contain many files: `manager.add()` returns one `Download` per file.

One-shot helper:

```js
import { download } from 'universal-downloader';
const paths = await download(['https://gofile.io/d/abc123'], { dir: './out' });
```

### Progress snapshot

`{ id, state, fileName, downloaded, total, percent, remaining, speed, eta, etaText }`

Bytes are numbers (use `formatBytes`); `eta` is in seconds and `etaText` is formatted as `45s`, `3m 20s`, `1h 5m` or `2d 3h`.

## CLI

```bash
npx udl <url...> -o ./downloads -c 3
```

Keys while running: `p` pause all, `r` resume all, `q` quit (partial files are kept).

## Writing a provider

```js
const myHost = {
  name: 'myhost',
  matches: (url) => /myhost\.com\/f\//.test(url),
  async resolve(url, { fetch, password }) {
    // return one source per file
    return [{ id: 'unique-id', url: 'https://cdn.../file.zip', filename: 'file.zip', size: 123, headers: {} }];
  },
};

new DownloadManager({ providers: [myHost] });
```

## Tests

```bash
npm test
```

## Responsible use

Only download content you have the right to access. Respect each service's terms; this library does not bypass captchas, paywalls or premium limits.

## License

MIT
