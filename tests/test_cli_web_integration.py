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

        # Verify Live SSE Stream
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:5099/api/stream")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("content-type"))
            line1 = resp.readline().decode()
            line2 = resp.readline().decode()
            self.assertIn("event: init", line1)
            self.assertIn("integration test", line2)

        # Push mock log event
        q.put({
            "worker_id": "INIT",
            "event": "log",
            "timestamp": "12:00:00",
            "prefix": "INIT",
            "message": "Testing log streaming",
            "color": "cyan"
        })
        time.sleep(0.3)
        snap_after_log = state.get_snapshot()
        self.assertGreater(len(snap_after_log["logs"]), 0)
        self.assertEqual(snap_after_log["logs"][-1]["message"], "Testing log streaming")

        # Verify /api/logs endpoint
        req_logs = urllib.request.Request("http://127.0.0.1:5099/api/logs")
        with urllib.request.urlopen(req_logs, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)

        # Verify /api/refresh-proxies endpoint
        req_ref = urllib.request.Request("http://127.0.0.1:5099/api/refresh-proxies", method="POST")
        with urllib.request.urlopen(req_ref, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)

        # Send shutdown event
        q.put({"event": "SHUTDOWN"})

if __name__ == "__main__":
    unittest.main()
