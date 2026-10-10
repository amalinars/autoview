import unittest
from geonode_fetcher import (
    fetch_iplocate_free_list,
    fetch_iplocate_proxies,
    load_pagination_state,
    save_pagination_state
)

class TestIPLocateIngestion(unittest.TestCase):
    def test_fetch_iplocate_free_list_structure(self):
        proxies, total = fetch_iplocate_free_list(limit=30)
        self.assertIsInstance(proxies, list)
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 30)

        for p in proxies:
            self.assertTrue(
                p.startswith(("http://", "https://", "socks4://", "socks5://")),
                f"Invalid IPLocate proxy format: {p}"
            )
            self.assertIn(":", p.split("://")[1])

    def test_fetch_iplocate_proxies_wrapper(self):
        proxies = fetch_iplocate_proxies(limit=25, timeout=5.0)
        self.assertIsInstance(proxies, list)
        self.assertGreater(len(proxies), 0)
        self.assertLessEqual(len(proxies), 25)

    def test_fetch_iplocate_countries_filter(self):
        proxies, total = fetch_iplocate_free_list(limit=20, countries=["US"])
        self.assertIsInstance(proxies, list)
        self.assertGreater(total, 0)
        self.assertGreater(len(proxies), 0)

    def test_fetch_iplocate_circular_wrap(self):
        state = load_pagination_state()
        state["iplocate_offset"] = 999999
        save_pagination_state(state)

        proxies, total = fetch_iplocate_free_list(limit=15)
        self.assertGreater(len(proxies), 0)
        new_state = load_pagination_state()
        self.assertLess(new_state["iplocate_offset"], total)

if __name__ == "__main__":
    unittest.main()
