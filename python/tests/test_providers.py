from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict

from universal_downloader import (Context, DownloadManager, HttpClient, ProviderError, find_provider,
                                  format_bytes, format_duration)
from universal_downloader.http import Response
from universal_downloader.providers.dropbox import dropbox
from universal_downloader.providers.gofile import build_website_token
from universal_downloader.providers.googledrive import googledrive
from universal_downloader.providers.krakenfiles import krakenfiles
from universal_downloader.providers.mediafire import extract_download_link, mediafire
from universal_downloader.providers.swisstransfer import swisstransfer
from universal_downloader.providers.tmpfiles import tmpfiles
from universal_downloader.providers.wetransfer import wetransfer

from .support import FakeClient, RangeServer, html_response, json_response, wait_for

LINK_ID = "01a18222-c204-7186-9a3f-790467825b55"


def context(client: FakeClient, password: str = None) -> Context:
    return Context(client, password)


class RoutingTests(unittest.TestCase):
    CASES = [
        ("https://drive.google.com/file/d/1AbC_dEf/view?usp=sharing", "googledrive"),
        ("https://www.dropbox.com/scl/fi/abc/file.zip?rlkey=x&dl=0", "dropbox"),
        ("https://we.tl/t-abc123", "wetransfer"),
        ("https://wetransfer.com/downloads/abc/def", "wetransfer"),
        ("https://krakenfiles.com/view/AbC123/file.html", "krakenfiles"),
        ("https://tmpfiles.org/12345/file.zip", "tmpfiles"),
        ("https://www.mediafire.com/folder/abcd1234/Stuff", "mediafire"),
        ("https://www.mediafire.com/file/abc/name.zip/file", "mediafire"),
        ("https://gofile.io/d/AbC123", "gofile"),
        (f"https://www.swisstransfer.com/d/{LINK_ID}", "swisstransfer"),
        ("https://files.catbox.moe/abc123.zip", "direct"),
        ("https://litter.catbox.moe/abc123.zip", "direct"),
        ("https://a.uguu.se/abc.zip", "direct"),
        ("https://0x0.st/abcd.bin", "direct"),
        ("https://transfer.sh/abc/file.zip", "direct"),
    ]

    def test_routes_each_link_to_the_right_provider(self) -> None:
        for url, expected in self.CASES:
            with self.subTest(url=url):
                self.assertEqual(find_provider(url).name, expected)

    def test_rejects_non_http_urls(self) -> None:
        with self.assertRaises(ProviderError):
            find_provider("ftp://example.com/a")


class FormatterTests(unittest.TestCase):
    def test_formats_bytes(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1536), "1.50 KB")
        self.assertEqual(format_bytes(5 * 1024 ** 3), "5.00 GB")
        self.assertEqual(format_bytes(None), "?")

    def test_formats_durations_in_seconds_minutes_hours_and_days(self) -> None:
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(200), "3m 20s")
        self.assertEqual(format_duration(3900), "1h 5m")
        self.assertEqual(format_duration(2 * 86400 + 3 * 3600), "2d 3h")
        self.assertEqual(format_duration(float("inf")), "?")


class DropboxAndTmpfilesTests(unittest.TestCase):
    def test_dropbox_forces_dl_1_and_drops_raw(self) -> None:
        (source,) = dropbox.resolve("https://www.dropbox.com/scl/fi/abc/my%20file.zip?rlkey=k&dl=0&raw=1", None)

        self.assertIn("dl=1", source.url)
        self.assertNotIn("raw=", source.url)
        self.assertIn("rlkey=k", source.url)
        self.assertEqual(source.filename, "my file.zip")

    def test_dropbox_leaves_the_name_to_the_server_for_folders(self) -> None:
        (source,) = dropbox.resolve("https://www.dropbox.com/scl/fo/abc/xyz?rlkey=k", None)
        self.assertIsNone(source.filename)

    def test_tmpfiles_rewrites_the_landing_page_and_keeps_direct_urls(self) -> None:
        (source,) = tmpfiles.resolve("https://tmpfiles.org/12345/file.zip", None)
        self.assertEqual(source.url, "https://tmpfiles.org/dl/12345/file.zip")
        self.assertEqual(source.filename, "file.zip")
        (direct,) = tmpfiles.resolve("https://tmpfiles.org/dl/12345/file.zip", None)
        self.assertEqual(direct.url, "https://tmpfiles.org/dl/12345/file.zip")


class GoogleDriveTests(unittest.TestCase):
    LINK = "https://drive.google.com/file/d/1AbC_dEf/view"

    @staticmethod
    def zip_response(*_args: Any) -> Response:
        return Response(200, b"data", {"content-type": "application/zip", "content-length": "4",
                                       "content-disposition": 'attachment; filename="big.zip"'})

    def test_uses_the_direct_download_when_drive_serves_the_file_right_away(self) -> None:
        client = FakeClient([("drive.usercontent.google.com", self.zip_response)])

        (source,) = googledrive.resolve(self.LINK, context(client))

        self.assertEqual((source.filename, source.size), ("big.zip", 4))
        self.assertIn("id=1AbC_dEf", source.url)

    def test_follows_the_virus_scan_confirmation_form(self) -> None:
        page = ('<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
                '<input type="hidden" name="id" value="1AbC_dEf"><input type="hidden" name="confirm" value="t">'
                '<input type="hidden" name="uuid" value="u-123"></form>')
        client = FakeClient([("uuid=", self.zip_response), ("drive.usercontent.google.com", lambda *_: html_response(page))])

        (source,) = googledrive.resolve(self.LINK, context(client))

        self.assertIn("uuid=u-123", source.url)
        self.assertEqual(source.filename, "big.zip")

    def test_explains_that_folders_are_unsupported(self) -> None:
        with self.assertRaises(ProviderError):
            googledrive.resolve("https://drive.google.com/drive/folders/abc", context(FakeClient([])))

    def test_fails_clearly_when_the_file_is_private(self) -> None:
        client = FakeClient([(".", lambda *_: html_response("<html>Sign in</html>"))])
        with self.assertRaises(ProviderError):
            googledrive.resolve(self.LINK, context(client))


class WeTransferTests(unittest.TestCase):
    def test_follows_we_tl_fetches_the_csrf_token_and_posts_the_security_hash(self) -> None:
        final = "https://wetransfer.com/downloads/tid123/hash456"
        client = FakeClient([
            (r"we\.tl", lambda *_: Response(200, b"", url=final)),
            (r"api/v4/transfers/tid123/download", lambda *_: json_response({"direct_link": "https://cdn.example/file.zip"})),
            (r"wetransfer\.com/$", lambda *_: Response(200, b'<meta name="csrf-token" content="TOKEN42">',
                                                       {"content-type": "text/html"},
                                                       set_cookies=["_wt_session=abc; Path=/; HttpOnly"])),
        ])

        (source,) = wetransfer.resolve("https://we.tl/t-abc", context(client))

        self.assertEqual(source.url, "https://cdn.example/file.zip")
        post = next(call for call in client.calls if call["url"].endswith("/download"))
        self.assertEqual(post["method"], "POST")
        self.assertEqual(post["headers"]["x-csrf-token"], "TOKEN42")
        self.assertEqual(post["headers"]["Cookie"], "_wt_session=abc")
        self.assertEqual(json.loads(post["data"]), {"intent": "entire_transfer", "security_hash": "hash456"})

    def test_includes_the_recipient_id_when_present(self) -> None:
        client = FakeClient([(r"wetransfer\.com/$", lambda *_: Response(200, b"")),
                             ("download", lambda *_: json_response({"direct_link": "https://cdn.example/x"}))])

        wetransfer.resolve("https://wetransfer.com/downloads/t/r/h", context(client))

        self.assertEqual(json.loads(client.calls[-1]["data"])["recipient_id"], "r")

    def test_reports_expired_transfers(self) -> None:
        client = FakeClient([(r"wetransfer\.com/$", lambda *_: Response(200, b"")),
                             ("download", lambda *_: json_response({"message": "expired"}))])
        with self.assertRaisesRegex(ProviderError, "expired"):
            wetransfer.resolve("https://wetransfer.com/downloads/t/h", context(client))


class KrakenfilesTests(unittest.TestCase):
    def test_posts_the_page_token_and_returns_the_signed_url(self) -> None:
        page = '<input type="hidden" id="dl-token" value="TOK"><div data-file-hash="HASH1"></div>'
        client = FakeClient([
            ("/view/", lambda *_: html_response(page)),
            ("/download/HASH1", lambda *_: json_response({"url": "https://s1.krakenfiles.com/force-download/abc/file.zip"})),
        ])

        (source,) = krakenfiles.resolve("https://krakenfiles.com/view/AbC/file.html", context(client))

        self.assertEqual(source.url, "https://s1.krakenfiles.com/force-download/abc/file.zip")
        post = next(call for call in client.calls if call["method"] == "POST")
        self.assertEqual(post["headers"]["hash"], "HASH1")
        self.assertIn(b'name="token"\r\n\r\nTOK\r\n', post["data"])

    def test_fails_clearly_when_the_page_has_no_token(self) -> None:
        client = FakeClient([(".", lambda *_: html_response("<html></html>"))])
        with self.assertRaises(ProviderError):
            krakenfiles.resolve("https://krakenfiles.com/view/AbC/file.html", context(client))


class MediaFireTests(unittest.TestCase):
    REAL = "https://download1234.mediafire.com/abc/file.zip"

    def test_reads_the_scrambled_base64_link(self) -> None:
        scrambled = base64.b64encode(self.REAL.encode()).decode()
        page = f'<a class="input popsok" id="downloadButton" href="#" data-scrambled-url="{scrambled}">Download</a>'
        self.assertEqual(extract_download_link(page), self.REAL)

    def test_falls_back_to_the_plain_href_and_decodes_entities(self) -> None:
        page = f'<a class="input popsok" href="{self.REAL}?a=1&amp;b=2" id="downloadButton">Download</a>'
        self.assertEqual(extract_download_link(page), f"{self.REAL}?a=1&b=2")

    def test_returns_none_when_the_page_has_no_button(self) -> None:
        self.assertIsNone(extract_download_link("<html></html>"))

    def test_resolves_a_page_through_the_client(self) -> None:
        client = FakeClient([(".", lambda *_: html_response(f'<a id="downloadButton" href="{self.REAL}">Download</a>'))])

        (source,) = mediafire.resolve("https://www.mediafire.com/file/x/file.zip/file", context(client))

        self.assertEqual((source.url, source.filename), (self.REAL, "file.zip"))

    @staticmethod
    def folder_content(**extra: Any) -> Response:
        return json_response({"response": {"result": "Success", "folder_content": {"more_chunks": "no", **extra}}})

    def folder_api(self, url: str, *_args: Any) -> Response:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        key, kind = query["folder_key"][0], query["content_type"][0]
        if (key, kind) == ("root", "files"):
            return self.folder_content(files=[{"filename": "a.zip", "size": "5",
                                               "links": {"normal_download": "https://www.mediafire.com/file/a/a.zip/file"}}])
        if (key, kind) == ("root", "folders"):
            return self.folder_content(folders=[{"folderkey": "sub", "name": "Inner"}])
        if (key, kind) == ("sub", "files"):
            return self.folder_content(files=[{"filename": "b.zip", "size": "7",
                                               "links": {"normal_download": "https://www.mediafire.com/file/b/b.zip/file"}}])
        return self.folder_content(folders=[])

    def test_lists_files_and_subfolders_through_the_public_api(self) -> None:
        def page(name: str):
            return lambda *_: html_response(f'<a id="downloadButton" href="https://download1.mediafire.com/x/{name}">Download</a>')

        client = FakeClient([("folder/get_content", self.folder_api), ("file/a/", page("a.zip")), ("file/b/", page("b.zip"))])

        sources = mediafire.resolve("https://www.mediafire.com/folder/root/Stuff", context(client))

        self.assertEqual([(item.filename, item.subdir) for item in sources], [("a.zip", None), ("b.zip", "Inner")])
        self.assertEqual(sources[1].url, "https://download1.mediafire.com/x/b.zip")
        self.assertEqual(sources[1].size, 7)

    def test_surfaces_api_errors_as_provider_errors(self) -> None:
        client = FakeClient([("get_content", lambda *_: json_response(
            {"response": {"result": "Error", "message": "Unknown or invalid FolderKey"}}))])
        with self.assertRaisesRegex(ProviderError, "FolderKey"):
            mediafire.resolve("https://www.mediafire.com/folder/bad/x", context(client))


class SwissTransferTests(unittest.TestCase):
    URL = f"https://www.swisstransfer.com/d/{LINK_ID}"

    @staticmethod
    def props(files: Any) -> Dict[str, Any]:
        return {"component": "link/show", "props": {"transfer": {"files": files}}}

    def page_client(self, body: str, status: int = 200) -> FakeClient:
        return FakeClient([(".", lambda *_: Response(status, body.encode(), {"content-type": "text/html"}))])

    def test_maps_the_page_files_to_sources_with_a_second_step(self) -> None:
        data = self.props([{"id": "f1", "path": "Docs/Sub/a.zip", "size": 42}, {"id": "f2", "path": "b.txt", "size": 1}])
        client = self.page_client(f'<script data-page="app" type="application/json">{json.dumps(data)}</script>')

        sources = swisstransfer.resolve(self.URL, context(client))

        self.assertTrue(callable(sources[0].resolve_url))
        self.assertEqual(sources[0].url, f"https://www.swisstransfer.com/api/1/links/{LINK_ID}/files/f1")
        self.assertEqual((sources[0].filename, sources[0].subdir, sources[0].size), ("a.zip", "Docs/Sub", 42))
        self.assertIsNone(sources[1].subdir)
        self.assertEqual(client.calls[0]["url"], f"https://www.swisstransfer.com/dl/{LINK_ID}")
        self.assertNotIn("X-Inertia", client.calls[0]["headers"])

    def test_reads_the_attribute_form_and_plain_json(self) -> None:
        data = self.props([{"id": "f9", "path": "z.zip", "size": 5}])
        escaped = json.dumps(data).replace('"', "&quot;")
        for body in (f'<div id="app" data-page="{escaped}"></div>', json.dumps(data)):
            with self.subTest(body=body[:20]):
                (source,) = swisstransfer.resolve(self.URL, context(self.page_client(body)))
                self.assertEqual(source.filename, "z.zip")

    def test_ignores_the_app_label_of_the_data_page_attribute(self) -> None:
        data = self.props([{"id": "f7", "path": "Game/pack.zip", "size": 9}])
        client = self.page_client(f'<script data-page="app" type="application/json">{json.dumps(data)}</script>')
        (source,) = swisstransfer.resolve(self.URL, context(client))
        self.assertEqual((source.filename, source.subdir), ("pack.zip", "Game"))

    def test_gives_provider_errors_for_missing_data_passwords_and_404(self) -> None:
        with self.assertRaises(ProviderError):
            swisstransfer.resolve(self.URL, context(self.page_client("<html>nothing</html>")))
        locked = json.dumps({"component": "link/password", "props": {}})
        with self.assertRaisesRegex(ProviderError, "password"):
            swisstransfer.resolve(self.URL, context(self.page_client(locked)))

        def not_found(*_args: Any) -> Response:
            from universal_downloader import HttpError

            raise HttpError(404, "Not Found")

        with self.assertRaises(ProviderError):
            swisstransfer.resolve(self.URL, context(FakeClient([(".", not_found)])))


class HybridClient:
    def __init__(self, fake: FakeClient):
        self.fake = fake
        self.real = HttpClient()

    def open(self, url: str, **options: Any):
        if "swisstransfer.com" in url:
            return self.fake.open(url, **options)
        return self.real.open(url, **options)

    def request(self, url: str, **options: Any):
        with self.open(url, **options) as response:
            return Response(response.status, response.read(), response.headers, response.url, response.set_cookies)


class SwissTransferEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="udl-test-")
        self.payload = os.urandom(256 * 1024)
        self.server = RangeServer(self.payload, delay=0.025)

    def tearDown(self) -> None:
        self.server.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_follows_the_signed_url_keeps_cookies_away_from_storage_and_re_signs_on_resume(self) -> None:
        signed = {"count": 0}
        data = {"component": "link/show", "props": {"transfer": {"files": [
            {"id": "f1", "path": "pack.bin", "size": len(self.payload)}]}}}

        def page(*_args: Any) -> Response:
            return Response(200, f'<script data-page="app" type="application/json">{json.dumps(data)}</script>'.encode(),
                            {"content-type": "text/html"}, set_cookies=["ST_SESSION=secret; Path=/"])

        def sign(url: str, method: str, headers: Dict[str, str], body: Any) -> Response:
            signed["count"] += 1
            assert "ST_SESSION=secret" in headers["Cookie"]
            return json_response({"result": "success", "data": {"url": self.server.url("signed.bin")}})

        fake = FakeClient([(rf"/dl/{LINK_ID}$", page), (rf"/api/1/links/{LINK_ID}/files/f1$", sign)])
        manager = DownloadManager(directory=self.dir, client=HybridClient(fake), progress_interval=0)

        (download,) = manager.add(f"https://www.swisstransfer.com/d/{LINK_ID}")
        wait_for(lambda: download.progress.downloaded > 64 * 1024)
        download.pause()
        download.resume()
        saved = download.wait(timeout=30)

        with open(saved, "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        self.assertEqual(signed["count"], 2)
        for request in self.server.requests:
            self.assertEqual(request["url"], "/signed.bin")


class GoFileTests(unittest.TestCase):
    def test_builds_a_stable_website_token_for_the_same_four_hour_slot(self) -> None:
        now = 1_767_268_800.0
        self.assertEqual(build_website_token("tok", "UA", now), build_website_token("tok", "UA", now + 60))
        self.assertNotEqual(build_website_token("tok", "UA", now), build_website_token("tok", "UA", now + 5 * 3600))
        self.assertRegex(build_website_token("tok", "UA", now), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
