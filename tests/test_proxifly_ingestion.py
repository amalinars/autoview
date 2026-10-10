import unittest
from geonode_fetcher import fetch_proxifly_free_list, fetch_proxifly_proxies

class TestProxiflyIngestion(unittest.TestCase):
    def test_fetch_proxifly_free_list_structure(self):
        proxies, total = fetch_proxifly_free_list(limit=20, timeout=10.0)
        self.assertIsInstance(proxies, list)
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 20)

        for p in proxies:
            self.assertTrue(
                p.startswith(("http://", "https://", "socks4://", "socks5://")),
                f"Invalid proxy format: {p}"
            )
            self.assertIn(":", p.split("://")[1])

    def test_fetch_proxifly_proxies_wrapper(self):
        proxies = fetch_proxifly_proxies(timeout=5.0)
        self.assertIsInstance(proxies, list)
        self.assertGreater(len(proxies), 0)

if __name__ == "__main__":
    unittest.main()
