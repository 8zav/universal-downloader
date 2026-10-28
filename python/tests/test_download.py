from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from universal_downloader import CancelledError, Download, DownloadManager, HttpError, ProviderError, Source

from .support import FakeClient, RangeServer, html_response, json_response, wait_for

PAYLOAD = os.urandom(512 * 1024)


def read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="udl-test-")
        self.server = None

    def tearDown(self) -> None:
        if self.server:
            self.server.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, **options: float) -> RangeServer:
        self.server = RangeServer(PAYLOAD, **options)
        return self.server

    def download(self, server: RangeServer, name: str = "file.bin", **options) -> Download:
        source = Source(id=name, url=server.url(name), filename=name)
        return Download(source, directory=self.dir, progress_interval=0, **options)


class DownloadTests(ServerCase):
    def test_downloads_a_file_and_reports_completed_progress(self) -> None:
        download = self.download(self.serve(), "a.bin")

        saved = download.start().wait(timeout=20)

        self.assertEqual(saved, os.path.join(self.dir, "a.bin"))
        self.assertEqual(read(saved), PAYLOAD)
        progress = download.progress
        self.assertEqual((progress.state, progress.percent, progress.remaining), ("completed", 100.0, 0))
        self.assertEqual((progress.eta, progress.eta_text), (0.0, "0s"))

    def test_reports_speed_remaining_bytes_and_eta_while_downloading(self) -> None:
        download = self.download(self.serve(delay=0.02), "p.bin")
        snapshots = []
        download.on("progress", snapshots.append)

        download.start().wait(timeout=30)

        midway = [item for item in snapshots if item.speed > 0 and item.state == "downloading"]
        self.assertTrue(midway)
        self.assertTrue(all(item.total == len(PAYLOAD) for item in midway))
        self.assertTrue(any(item.eta and item.eta > 0 and item.remaining for item in midway))

    def test_knows_speed_and_eta_at_the_first_progress_event(self) -> None:
        download = self.download(self.serve(delay=0.02), "e.bin")
        snapshots = []
        download.on("progress", snapshots.append)

        download.start().wait(timeout=30)

        self.assertGreater(snapshots[0].speed, 0)
        self.assertNotEqual(snapshots[0].eta_text, "?")

    def test_pauses_then_resumes_with_a_range_request(self) -> None:
        server = self.serve(delay=0.025)
        download = self.download(server, "r.bin")
        download.start()

        wait_for(lambda: download.progress.downloaded > 64 * 1024)
        self.assertTrue(download.pause())
        self.assertEqual(download.state, "paused")
        part_size = os.path.getsize(os.path.join(self.dir, "r.bin.part"))
        self.assertTrue(0 < part_size < len(PAYLOAD))

        self.assertTrue(download.resume())
        saved = download.wait(timeout=30)

        self.assertEqual(read(saved), PAYLOAD)
        self.assertTrue(any(request["range"] and request["range"].startswith("bytes=") for request in server.requests))

    def test_restarts_from_zero_when_the_server_ignores_range(self) -> None:
        download = self.download(self.serve(delay=0.025, honor_range=False), "n.bin")
        download.start()

        wait_for(lambda: download.progress.downloaded > 64 * 1024)
        download.pause()
        download.resume()

        self.assertEqual(read(download.wait(timeout=30)), PAYLOAD)

    def test_cancel_deletes_the_partial_file_and_wait_raises(self) -> None:
        download = self.download(self.serve(delay=0.025), "c.bin")
        download.start()

        wait_for(lambda: download.progress.downloaded > 32 * 1024)
        download.cancel()

        with self.assertRaises(CancelledError):
            download.wait(timeout=5)
        self.assertEqual(download.state, "cancelled")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "c.bin.part")))

    def test_fails_without_retrying_on_a_404(self) -> None:
        def not_found(*_args):
            raise HttpError(404, "Not Found")

        client = FakeClient([(".", not_found)])
        download = Download(Source(id="x", url="http://example.invalid/x.bin", filename="x.bin"),
                            directory=self.dir, retries=0, client=client)

        with self.assertRaises(HttpError) as raised:
            download.start().wait(timeout=5)
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(download.state, "failed")

    def test_refuses_to_save_a_web_page_as_the_file(self) -> None:
        client = FakeClient([(".", lambda *_: html_response("<!DOCTYPE html><title>Home</title>"))])
        download = Download(Source(id="g", url="http://example.invalid/dl/abc", filename="game.zip"),
                            directory=self.dir, client=client)

        with self.assertRaisesRegex(ProviderError, "web page"):
            download.start().wait(timeout=5)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "game.zip")))

    def test_rejects_an_api_json_answer_saved_in_place_of_the_file(self) -> None:
        client = FakeClient([(".", lambda *_: json_response({"result": "success", "data": {"url": "x"}}))])
        download = Download(Source(id="g", url="http://example.invalid/api", filename="game.lua"),
                            directory=self.dir, client=client)

        with self.assertRaises(ProviderError):
            download.start().wait(timeout=5)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "game.lua.part")))

    def test_never_writes_outside_the_download_dir(self) -> None:
        server = self.serve()
        source = Source(id="t", url=server.url(), filename="../../evil.bin", subdir="../..")
        download = Download(source, directory=self.dir)

        saved = download.start().wait(timeout=20)

        self.assertTrue(os.path.abspath(saved).startswith(os.path.abspath(self.dir)))


class ManagerTests(ServerCase):
    def test_respects_the_concurrency_limit_and_finishes_every_file(self) -> None:
        server = self.serve(delay=0.005)
        manager = DownloadManager(directory=self.dir, concurrency=2)
        peak = {"value": 0}

        def track(*_args) -> None:
            running = sum(1 for item in manager.downloads if item.state == "downloading")
            peak["value"] = max(peak["value"], running)

        manager.on("state", track)

        batches = [manager.add(server.url(f"f{n}.bin")) for n in range(1, 6)]
        self.assertTrue(manager.wait_idle(timeout=60))

        self.assertEqual(len(batches), 5)
        self.assertEqual(peak["value"], 2)
        self.assertEqual(manager.stats().completed, 5)
        for (item,) in batches:
            self.assertEqual(read(item.file_path), PAYLOAD)

    def test_a_paused_download_frees_its_slot_for_the_next_one(self) -> None:
        server = self.serve(delay=0.02)
        manager = DownloadManager(directory=self.dir, concurrency=1)
        (first,) = manager.add(server.url("one.bin"))
        (second,) = manager.add(server.url("two.bin"))

        wait_for(lambda: first.progress.downloaded > 32 * 1024)
        self.assertEqual(second.state, "queued")
        manager.pause(first)
        wait_for(lambda: second.state == "downloading")

        manager.resume(first)
        self.assertTrue(manager.wait_idle(timeout=60))

        self.assertEqual(read(first.file_path), PAYLOAD)
        self.assertEqual(read(second.file_path), PAYLOAD)

    def test_aggregates_speed_and_remaining_bytes(self) -> None:
        server = self.serve(delay=0.02)
        manager = DownloadManager(directory=self.dir, concurrency=2, progress_interval=0)
        manager.add(server.url("s1.bin"))
        manager.add(server.url("s2.bin"))

        wait_for(lambda: manager.stats().speed > 0)
        stats = manager.stats()

        self.assertEqual(stats.active, 2)
        self.assertTrue(0 < stats.remaining < len(PAYLOAD) * 2)
        self.assertGreater(stats.eta, 0)
        manager.cancel_all()


if __name__ == "__main__":
    unittest.main()
