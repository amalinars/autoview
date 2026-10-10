import unittest
from geonode_fetcher import (
    circular_slice,
    get_next_pagination_slice,
    save_pagination_state,
    load_pagination_state,
    SORT_MODES
)

class TestCircularPagination(unittest.TestCase):
    def test_circular_slice_basic(self):
        items = list(range(100))
        # Normal slice within bounds
        res, next_off, wrapped = circular_slice(items, offset=0, limit=20)
        self.assertEqual(res, list(range(20)))
        self.assertEqual(next_off, 20)
        self.assertFalse(wrapped)

    def test_circular_slice_boundary_wrap(self):
        items = list(range(100))
        # Slice that crosses boundary
        res, next_off, wrapped = circular_slice(items, offset=90, limit=20)
        # Should take 90..99 (10 items) and 0..9 (10 items) = 20 items
        expected = list(range(90, 100)) + list(range(0, 10))
        self.assertEqual(res, expected)
        self.assertEqual(next_off, 10)
        self.assertTrue(wrapped)

    def test_circular_slice_offset_exceeding_total(self):
        items = list(range(50))
        # Offset 120 on 50 items -> 120 % 50 = 20
        res, next_off, wrapped = circular_slice(items, offset=120, limit=10)
        self.assertEqual(res, list(range(20, 30)))
        self.assertEqual(next_off, 30)
        self.assertTrue(wrapped)

    def test_geonode_pagination_slice_wrap_around(self):
        # Setup state to last page
        state = load_pagination_state()
        state["total_available"] = 500
        state["page"] = 3
        state["mode_index"] = 0
        save_pagination_state(state)

        # per_page = 250, total = 500 -> max_pages = 2
        # Since page=3 > max_pages=2, it must wrap back to page=1 and advance mode_index
        p, s_by, s_type, desc, start_item, end_item = get_next_pagination_slice(per_page=250)
        self.assertEqual(p, 1)
        self.assertEqual(start_item, 1)
        self.assertEqual(end_item, 250)
        self.assertEqual(desc, SORT_MODES[1][2])

    def test_all_provider_offsets_in_state(self):
        state = load_pagination_state()
        for key in ["page", "proxyscrape_offset", "proxifly_offset", "thespeedx_offset", "iplocate_offset"]:
            self.assertIn(key, state)
            self.assertIsInstance(state[key], int)

if __name__ == "__main__":
    unittest.main()
