"""Frontend i18n regression: parameter values must be inserted literally.

`String.replace(pattern, params[p])` treats ``$&``/``$'``/``$``` as replacement
patterns, so a goal named ``$&`` used to render the literal ``{name}``. Run the real
``i18n.js`` in Node when available; skip on machines without Node.
"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N_PATH = os.path.join(ROOT, "static", "js", "i18n.js")
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is required for the frontend i18n regression test")
class I18nDollarInterpolationTest(unittest.TestCase):
    def test_dollar_sequences_are_literal_in_goal_names(self):
        names = ["$&", "$'", "$`", "$$", "Ada"]
        script = (
            "global.window = {};"
            f"require({json.dumps(I18N_PATH)});"
            "const t = global.window.i18n.t;"
            f"const names = {json.dumps(names)};"
            "process.stdout.write(JSON.stringify(names.map((name) => "
            "[name, t('en', 'chat_goal_progress', "
            "{name, pct: 0, current: '¥0.00', target: '¥1.00'})])));"
        )
        result = subprocess.run(
            [NODE, "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = dict(json.loads(result.stdout))
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, rendered[name])
                self.assertNotIn("{name}", rendered[name])


if __name__ == "__main__":
    unittest.main()
