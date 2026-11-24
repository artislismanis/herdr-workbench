import atexit
import os
import shutil
import tempfile
import unittest

# Every temp folder a test makes lands under one root, which goes when the run ends, so tests leave nothing in /tmp.
ROOT = tempfile.mkdtemp(prefix="workshop-tests-")
os.environ["TMPDIR"] = tempfile.tempdir = ROOT
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)

# Discovery imports every test module before any test runs, so this keeps the whole suite off your own
# ~/.config/workshop pipelines and prompts, for every repo or one, which would otherwise replace the shipped ones under test.
for name in ("WORKSHOP_PIPELINES", "WORKSHOP_PROMPTS", "WORKSHOP_REPOS"):
    os.environ[name] = tempfile.mkdtemp()


class Isolation(unittest.TestCase):
    def test_tests_never_read_your_own_pipelines_or_prompts(self):
        for name in ("WORKSHOP_PIPELINES", "WORKSHOP_PROMPTS", "WORKSHOP_REPOS"):
            self.assertTrue(os.environ[name].startswith(ROOT))

    def test_temp_folders_land_under_the_root(self):
        self.assertTrue(tempfile.mkdtemp().startswith(ROOT))
