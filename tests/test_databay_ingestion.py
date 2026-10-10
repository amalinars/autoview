import unittest
from geonode_fetcher import (
    fetch_databay_free_list,
    fetch_databay_proxies,
    load_pagination_state,
    save_pagination_state
)

class TestDatabayIngestion(unittest.TestCase):
    def test_fetch_databay_free_list_structure(self):
        proxies, total = fetch_databay_free_list(limit=25, timeout=10.0)
        self.assertIsInstance(proxies, list)
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 25)

        for p in proxies:
            self.assertTrue(
                p.startswith(("http://", "https://", "socks4://", "socks5://")),
                f"Invalid Databay proxy format: {p}"
            )
            self.assertIn(":", p.split("://")[1])

    def test_fetch_databay_proxies_wrapper(self):
        proxies = fetch_databay_proxies(limit=15, timeout=10.0)
        self.assertIsInstance(proxies, list)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 15)

    def test_fetch_databay_circular_wrap(self):
        state = load_pagination_state()
        state["databay_offset"] = 999999
        save_pagination_state(state)

        proxies, total = fetch_databay_free_list(limit=10, timeout=10.0)
        self.assertGreater(len(proxies), 0)
        new_state = load_pagination_state()
        self.assertLess(new_state["databay_offset"], total)

if __name__ == "__main__":
    unittest.main()
