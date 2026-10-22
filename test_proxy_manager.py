import tempfile
import unittest
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

from proxy_manager import (
    AuthenticatedProxyBridge,
    ProxyStore,
    format_bytes,
    format_duration,
    parse_proxy,
    read_proxy_file,
)


class PortablePathTests(unittest.TestCase):
    def test_frozen_app_keeps_data_beside_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder).resolve()
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "executable", str(app_dir / "ProxyDeck.exe")
            ):
                module = runpy.run_path(str(Path(__file__).with_name("proxy_manager.py")))
            self.assertEqual(module["APP_DIR"], app_dir)
            self.assertEqual(module["DEFAULT_PROXY_FILE"], app_dir / "proxies.txt")
            store = module["ProxyStore"]()
            store.add("proxy.example:8080:demo:placeholder")
            self.assertTrue((app_dir / "proxies.txt").is_file())
            self.assertTrue((app_dir / "proxydeck_data" / "state.json").is_file())


class ProxyParsingTests(unittest.TestCase):
    def test_supported_proxy_formats(self):
        plain = parse_proxy("127.0.0.1:8080")
        self.assertEqual(plain.server_url, "http://127.0.0.1:8080")
        self.assertEqual(plain.masked, "127.0.0.1:8080")

        authenticated = parse_proxy(
            "https://proxy.example:443:demo:secret:with-colon|HU"
        )
        self.assertEqual(authenticated.scheme, "https")
        self.assertEqual(authenticated.username, "demo")
        self.assertEqual(authenticated.password, "secret:with-colon")
        self.assertEqual(authenticated.region_hint, "HU")
        self.assertNotIn("secret", authenticated.masked)

    def test_invalid_proxy_formats(self):
        for value in ("", "host", "host:nope", "host:70000", "host:80:user:"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_proxy(value)

    def test_display_formatters(self):
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MB")
        self.assertEqual(format_duration(65), "1m 05s")
        self.assertEqual(format_duration(3660), "1h 01m")


class ProxyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.proxy_file = self.root / "proxies.txt"
        self.proxy_file.write_text(
            "# keep this comment\n127.0.0.1:8000:user:secret\n",
            encoding="utf-8",
        )
        self.store = ProxyStore(self.proxy_file, self.root / "data")

    def tearDown(self):
        self.temporary.cleanup()

    def test_lifecycle_and_secret_free_metadata(self):
        specs, errors = self.store.proxies()
        self.assertFalse(errors)
        self.assertEqual(len(specs), 1)
        spec = specs[0]

        self.store.mark_opened(spec)
        self.store.mark_closed(spec, 12.5, 100, 200)
        record = self.store.record(spec)
        self.assertEqual(record["launch_count"], 1)
        self.assertEqual(record["bytes_up"], 100)
        self.assertEqual(record["bytes_down"], 200)
        self.assertAlmostEqual(record["active_seconds"], 12.5)

        saved_state = self.store.state_file.read_text(encoding="utf-8")
        self.assertNotIn("secret", saved_state)
        self.assertNotIn("user", saved_state)

    def test_add_edit_remove_preserves_comments_and_stats(self):
        original = self.store.proxies()[0][0]
        added = self.store.add("10.0.0.2:9000|DE")
        self.store.update(added, alias="Berlin", bytes_down=1234)
        edited = self.store.edit(added, "10.0.0.3:9001|DE")
        migrated = self.store.record(edited)
        self.assertEqual(migrated["alias"], "Berlin")
        self.assertEqual(migrated["bytes_down"], 1234)

        self.store.remove(original)
        text = self.proxy_file.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", text)
        self.assertIn("10.0.0.3:9001|DE", text)
        self.assertNotIn("127.0.0.1:8000", text)

    def test_duplicate_is_rejected(self):
        self.store.proxies()
        with self.assertRaises(ValueError):
            self.store.add("HTTP://127.0.0.1:8000:user:secret")

    def test_import_reports_bad_lines(self):
        source = self.root / "more.txt"
        source.write_text("10.0.0.1:80\nbroken\n", encoding="utf-8")
        count, errors = self.store.import_file(source)
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)


class BridgeTests(unittest.TestCase):
    def test_proxy_authorization_is_injected_without_leaking_to_client(self):
        bridge = AuthenticatedProxyBridge(
            parse_proxy("proxy.example:8080:demo:s3cret")
        )
        request = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
        outgoing = bridge.with_authorization(request)
        self.assertIn(b"Proxy-Authorization: Basic", outgoing)
        self.assertNotIn(b"s3cret", outgoing)


if __name__ == "__main__":
    unittest.main()
