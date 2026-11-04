import os
import tempfile
import unittest

# Discovery imports every test module before any test runs, so this keeps the whole suite off your own
# ~/.config/workshop pipelines and prompts, for every repo or one, which would otherwise replace the shipped ones under test.
for name in ("WORKSHOP_PIPELINES", "WORKSHOP_PROMPTS", "WORKSHOP_REPOS"):
    os.environ[name] = tempfile.mkdtemp()


class Isolation(unittest.TestCase):
    def test_tests_never_read_your_own_pipelines_or_prompts(self):
        for name in ("WORKSHOP_PIPELINES", "WORKSHOP_PROMPTS", "WORKSHOP_REPOS"):
            self.assertTrue(os.environ[name].startswith(tempfile.gettempdir()))
