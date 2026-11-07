import tempfile
import unittest
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from unittest.mock import patch

from cookie_export import default_output_path, find_music_cookie, netscape_cookie, read_netscape_cookie, write_cookie


class CookieExportTests(unittest.TestCase):
    def setUp(self):
        self.cookie = {
            "name": "media-user-token",
            "domain": ".music.apple.com",
            "value": "fake.test.token",
            "expires": 2000000000,
        }

    def test_only_apple_music_token_is_selected(self):
        cookies = [
            {"name": "media-user-token", "domain": ".other.example", "value": "wrong"},
            {"name": "other", "domain": ".music.apple.com", "value": "wrong"},
            self.cookie,
        ]
        self.assertEqual(find_music_cookie(cookies), self.cookie)

    def test_export_is_readable_by_gamdl_cookiejar_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            write_cookie(path, self.cookie)
            jar = MozillaCookieJar(path)
            jar.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual([(c.domain, c.name, c.value) for c in jar], [
                (".music.apple.com", "media-user-token", "fake.test.token")
            ])
            with self.assertRaises(FileExistsError):
                write_cookie(path, self.cookie)

    def test_rejects_line_breaks_in_cookie_value(self):
        with self.assertRaises(ValueError):
            netscape_cookie({**self.cookie, "value": "x\nmalicious"})

    def test_import_discards_unrelated_cookies(self):
        data = (
            ".music.apple.com\tTRUE\t/\tTRUE\t2000000000\tother\tsecret\n"
            ".music.apple.com\tTRUE\t/\tTRUE\t2000000000\tmedia-user-token\tfake.test.token\n"
        ).encode()
        self.assertEqual(read_netscape_cookie(data), self.cookie)

    def test_frozen_helper_writes_beside_executable(self):
        with patch("cookie_export.sys.frozen", True, create=True), \
             patch("cookie_export.sys.executable", r"C:\Downloads\IridiumCookieExport.exe"):
            self.assertEqual(default_output_path(), Path(r"C:\Downloads\cookies.txt").resolve())
