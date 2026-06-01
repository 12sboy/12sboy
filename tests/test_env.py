import os
import tempfile
import unittest
from pathlib import Path

from trading_bot.env import load_dotenv


class EnvTests(unittest.TestCase):
    def test_load_dotenv_sets_missing_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("FOO_TEST_ENV=bar\n# comment\nBAZ_TEST_ENV='qux'\n")
            os.environ.pop("FOO_TEST_ENV", None)
            os.environ.pop("BAZ_TEST_ENV", None)
            load_dotenv(p)
            self.assertEqual(os.environ["FOO_TEST_ENV"], "bar")
            self.assertEqual(os.environ["BAZ_TEST_ENV"], "qux")


if __name__ == "__main__":
    unittest.main()
