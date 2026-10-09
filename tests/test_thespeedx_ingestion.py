import unittest
from geonode_fetcher import fetch_thespeedx_free_list, fetch_thespeedx_proxies

class TestTheSpeedXIngestion(unittest.TestCase):
    def test_fetch_thespeedx_free_list_structure(self):
        proxies, total = fetch_thespeedx_free_list(limit=30)
        self.assertIsInstance(proxies, list)
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 30)

        for p in proxies:
            self.assertTrue(
                p.startswith(("http://", "socks4://", "socks5://")),
                f"Invalid TheSpeedX proxy format: {p}"
            )
            self.assertIn(":", p.split("://")[1])

    def test_fetch_thespeedx_proxies_wrapper(self):
        proxies = fetch_thespeedx_proxies(limit=25, timeout=5.0)
        self.assertIsInstance(proxies, list)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 25)

if __name__ == "__main__":
    unittest.main()
