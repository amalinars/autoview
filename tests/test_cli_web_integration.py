import unittest
import multiprocessing
import time
from web_dashboard import start_web_dashboard

class TestCLIWebIntegration(unittest.TestCase):
    def test_web_dashboard_background_thread(self):
        q = multiprocessing.Queue()
        cfg = {"batch_count": 10, "workers": 2, "keyword": "integration test"}
        state, thread = start_web_dashboard(q, host="127.0.0.1", port=5099, initial_config=cfg)
        
        # Push mock event through queue
        q.put({
            "worker_id": 1,
            "event": "init",
            "pid": 9999,
            "status": "SIAP",
            "details": "Ready"
        })
        time.sleep(0.3)
        snap = state.get_snapshot()
        self.assertIn(1, snap["workers"])
        self.assertEqual(snap["workers"][1]["pid"], 9999)

        # Send shutdown event
        q.put({"event": "SHUTDOWN"})

if __name__ == "__main__":
    unittest.main()
