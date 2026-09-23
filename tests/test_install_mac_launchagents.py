import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/install_mac_launchagents.py"
spec = importlib.util.spec_from_file_location("install_mac_launchagents", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MacLaunchAgentInstallerTests(unittest.TestCase):
    def test_all_production_agents_use_stable_local_bootstrap(self):
        specs = module.specs()
        self.assertEqual(set(specs), {
            "local.oursteps.worker-status",
            "local.oursteps.control-actions",
            "local.oursteps.incremental-sync",
            "local.oursteps.guide-discovery",
            "local.oursteps.historical-backfill",
        })
        wrapper = str(module.BOOTSTRAP_TARGET)
        for config in specs.values():
            self.assertEqual(config["ProgramArguments"][0], wrapper)
            self.assertEqual(config["WorkingDirectory"], str(Path.home()))
            serialized = repr(config)
            self.assertNotIn("/Volumes/Newhome/docker/oursteps-archive", serialized)

    def test_current_production_schedules_are_preserved(self):
        specs = module.specs()
        inc = specs["local.oursteps.incremental-sync"]["StartCalendarInterval"]
        hist = specs["local.oursteps.historical-backfill"]["StartCalendarInterval"]
        guide = specs["local.oursteps.guide-discovery"]["StartCalendarInterval"]
        self.assertEqual([x["Hour"] for x in inc], list(range(6, 23, 2)))
        self.assertEqual([x["Hour"] for x in hist], list(range(7, 22, 2)))
        self.assertEqual(guide, {"Hour": 3, "Minute": 15})
        self.assertIn("20", specs["local.oursteps.historical-backfill"]["ProgramArguments"])
        self.assertIn("45", specs["local.oursteps.historical-backfill"]["ProgramArguments"])

    def test_bootstrap_source_handles_numbered_mounts(self):
        text = module.BOOTSTRAP_SOURCE.read_text()
        self.assertIn("setopt NULL_GLOB", text)
        self.assertIn("/Volumes/Newhome-*/docker/oursteps-archive", text)
        self.assertIn('[[ -d "$candidate/.git"', text)
        self.assertIn('mount volume "smb://DS923SOPAC.local/Newhome"', text)
        self.assertIn("/tmp/oursteps-smb-remount.lock", text)


if __name__ == "__main__":
    unittest.main()
