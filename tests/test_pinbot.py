import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pinbot import pins, products, site


class T(unittest.TestCase):
    def test_pin_link_has_utm(self):
        p = products.load_products()[0]
        pin = pins.build_pin(p, "https://x.io")
        self.assertIn("utm_source=pinterest", pin["link"])
        self.assertLessEqual(len(pin["description"]), 500)

    def test_build_site(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o"
            n = site.build(out)
            self.assertEqual(n, len(products.load_products()))
            self.assertIn("affiliate", (out / "index.html").read_text())
            self.assertTrue(any((out / "p").iterdir()))

    def test_validation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"slug": "a"}], f)
        with self.assertRaises(ValueError):
            products.load_products(Path(f.name))


if __name__ == "__main__":
    unittest.main()
