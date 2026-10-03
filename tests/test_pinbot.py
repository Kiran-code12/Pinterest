import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pinbot import articles, pins, products, site


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
            self.assertEqual(n, len(products.load_products()) + len(articles.load_articles()))
            self.assertIn("affiliate", (out / "index.html").read_text())
            self.assertTrue(any((out / "p").iterdir()))

    def test_guide_page_and_pin(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o"
            site.build(out)
            a = articles.load_articles()[0]
            self.assertIn("Products mentioned", (out / "a" / f"{a['slug']}.html").read_text())
            self.assertIn("/a/" + a["slug"], pins.build_pin(a, "https://x.io")["link"])

    def test_photo_pins(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            imgs, out = Path(d) / "img", Path(d) / "o"
            imgs.mkdir()
            Image.new("RGBA", (300, 500), (200, 50, 80, 255)).save(imgs / "x.png")
            prods = products.load_products()
            prods[0]["photo"] = "x.png"
            orig = products.load_products
            site.load_products = lambda: prods
            try:
                site.build(out, images_dir=imgs)
                self.assertEqual(Image.open(out / "pins" / f"{prods[0]['slug']}.png").size, (1000, 1500))
                prods[0]["photo"] = "missing.png"
                with self.assertRaises(ValueError):
                    site.build(out, images_dir=imgs)
            finally:
                site.load_products = orig

    def test_markdown(self):
        h = articles.md_to_html("## T\n\n- **a**\n- b\n\n<script>x</script>")
        self.assertIn("<li><strong>a</strong></li>", h)
        self.assertNotIn("<script>", h)

    def test_validation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"slug": "a"}], f)
        with self.assertRaises(ValueError):
            products.load_products(Path(f.name))


if __name__ == "__main__":
    unittest.main()
