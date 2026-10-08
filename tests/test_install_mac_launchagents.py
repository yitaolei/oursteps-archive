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
            "local.oursteps.keep-awake",
            "local.oursteps.worker-status",
            "local.oursteps.control-actions",
            "local.oursteps.incremental-sync",
            "local.oursteps.guide-discovery",
            "local.oursteps.historical-backfill",
        })
        wrapper = str(module.BOOTSTRAP_TARGET)
        for label, config in specs.items():
            if label == "local.oursteps.keep-awake":
                self.assertEqual(config["ProgramArguments"], ["/usr/bin/caffeinate", "-s"])
                self.assertTrue(config["RunAtLoad"])
                self.assertTrue(config["KeepAlive"])
            else:
                self.assertEqual(config["ProgramArguments"][0], wrapper)
            self.assertEqual(config["WorkingDirectory"], str(Path.home()))
            serialized = repr(config)
            self.assertNotIn("/Volumes/Newhome/docker/oursteps-archive", serialized)


    def test_network_orchestrators_use_local_control_plane(self):
        specs = module.specs()
        for label in module.CONTROL_LABELS:
            args = specs[label]["ProgramArguments"]
            self.assertEqual(args[1], "local-clean-nas-env")
        self.assertEqual(
            module.CONTROL_TARGET,
            Path.home() / "Library/Application Support/OurSteps/control-plane",
        )

    def test_current_production_schedules_are_preserved(self):
        specs = module.specs()
        inc = specs["local.oursteps.incremental-sync"]["StartCalendarInterval"]
        hist = specs["local.oursteps.historical-backfill"]["StartCalendarInterval"]
        guide = specs["local.oursteps.guide-discovery"]["StartCalendarInterval"]
        self.assertEqual([x["Hour"] for x in inc], list(range(6, 23, 2)))
        self.assertEqual([x["Hour"] for x in hist], list(range(7, 22, 2)))
        self.assertEqual(guide, {"Hour": 3, "Minute": 15})
        self.assertIn("120", specs["local.oursteps.historical-backfill"]["ProgramArguments"])
        self.assertIn("45", specs["local.oursteps.historical-backfill"]["ProgramArguments"])

    def test_bootstrap_source_handles_numbered_mounts(self):
        text = module.BOOTSTRAP_SOURCE.read_text()
        self.assertIn("setopt NULL_GLOB", text)
        self.assertIn("/Volumes/Newhome-*/docker/oursteps-archive", text)
        self.assertIn('[[ -d "$candidate/.git"', text)
        self.assertIn('mount volume "smb://DS923SOPAC.local/Newhome"', text)
        self.assertIn("/tmp/oursteps-smb-remount.lock", text)
        self.assertIn("local-clean-nas-env", text)
        self.assertIn("Library/Application Support/OurSteps/control-plane", text)


if __name__ == "__main__":
    unittest.main()
