import unittest
from pathlib import Path
import tempfile

from scripts.test_backend import BackendProbe, run


class TransportTests(unittest.TestCase):
    def test_process_roundtrip_continuous_input_restart(self):
        run()

    def test_bad_input_and_notifications_do_not_break_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = BackendProbe(Path(directory) / "test.sqlite3")
            try:
                probe.process.stdin.write('{bad json}\n')
                probe.process.stdin.flush()
                self.assertEqual(probe.wait(lambda e: "error" in e)["error"]["code"], -32700)
                # Unknown notifications receive no response. The next response acknowledges text.
                probe.process.stdin.write('{"jsonrpc":"2.0","method":"unknown"}\n')
                probe.process.stdin.flush()
                request_id = probe.send("chat/send", {"text": "继续聊天"})
                event = probe.wait(lambda e: True)
                self.assertEqual(event["id"], request_id)
                self.assertEqual(event["result"], {})
            finally:
                probe.close()
