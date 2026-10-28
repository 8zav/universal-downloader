# universal-downloader (Python)

Download files from file-sharing services with one API: **pause / resume**, **live speed**, **bytes left**, **ETA** and **concurrent downloads**. No dependencies (standard library only), Python 3.9+.

This is the Python port of the Node.js package in the same repository. Both share the same design and the same providers.

## Install

```bash
pip install universal-downloader
```

## Usage

```python
from universal_downloader import DownloadManager, format_bytes, format_speed

manager = DownloadManager(directory="./downloads", concurrency=3)

def show(download, p):
    print(f"{p.file_name} {p.percent or 0:.1f}% | {format_speed(p.speed)} | "
          f"left {format_bytes(p.remaining)} | ETA {p.eta_text}")

manager.on("progress", show)

(file,) = manager.add("https://www.mediafire.com/file/...")

file.pause()      # keeps the .part file
file.resume()     # continues with an HTTP Range request
file.cancel()     # stops and deletes the partial file

manager.wait_idle()
print(manager.stats())   # total speed, bytes left and ETA for the whole queue
```

A share link can contain many files: `manager.add()` returns one `Download` per file. Downloads run in worker threads; `download.wait()` blocks until the file is saved or raises the failure.

One-shot helper:

```python
from universal_downloader import download

paths = download(["https://gofile.io/d/abc123"], directory="./out")
```

### Progress snapshot

`Progress(id, state, file_name, downloaded, total, percent, remaining, speed, eta, eta_text)`

Bytes are integers (use `format_bytes`); `eta` is in seconds and `eta_text` reads like `45s`, `3m 20s`, `1h 5m` or `2d 3h`.

## CLI

```bash
udl <url...> -o ./downloads -c 3
python -m universal_downloader <url...>
```

Keys while running: `p` pause all, `r` resume all, `q` quit (partial files are kept).

## Supported services

None of these need an API key.

| Service | Notes |
|---|---|
| Direct links: catbox / litterbox, uguu, 0x0.st, transfer.sh, GitHub releases, Discord and other CDNs | through the `direct` provider |
| MediaFire | files and folders (sub-folders included) |
| GoFile | folders and sub-folders, password supported; token scheme still being verified |
| SwissTransfer | all files of a transfer |
| Google Drive | public files, including large files with the virus-scan page; folders are not supported |
| Dropbox | shared files and folders (folders arrive as a zip) |
| WeTransfer | `wetransfer.com` and `we.tl` links |
| Krakenfiles | single files |
| tmpfiles.org | single files |

## Writing a provider

```python
from universal_downloader import DownloadManager, Source

class MyHost:
    name = "myhost"

    def matches(self, url):
        return "myhost.com/f/" in url

    def resolve(self, url, context):
        return [Source(id="unique-id", url="https://cdn.../file.zip", filename="file.zip", size=123)]

manager = DownloadManager(providers=[MyHost()])
```

`context.client` is the HTTP client and `context.password` the optional password. A `Source` can also carry `resolve_url`, a function called before every attempt (also on resume) for hosts that hand out short-lived signed URLs in a second step.

## Tests

```bash
pip install -e .
python -m unittest discover -s tests
```

## Responsible use

Only download content you have the right to access. Respect each service's terms; this library does not bypass captchas, paywalls or premium limits.

## License

MIT
