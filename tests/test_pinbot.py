import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pinbot import articles, fetch, pins, products, site


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

    def test_fetch_images(self):
        import http.server, io, threading
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (40, 60), (10, 120, 200)).save(buf, "PNG")
        png = buf.getvalue()

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path.split("?")[0] == "/img.png":
                    self.send_response(200); self.send_header("Content-Type", "image/png"); self.end_headers(); self.wfile.write(png)
                elif self.path == "/page":
                    body = b'<html><head><meta property="og:image" content="/img.png?x=1&amp;y=2"></head></html>'
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(404); self.end_headers()
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        with tempfile.TemporaryDirectory() as d:
            pf, imgs = Path(d) / "p.json", Path(d) / "imgs"
            pf.write_text(json.dumps([
                {"slug": "a", "image_source": base + "/page"},
                {"slug": "b", "image_source": base + "/img.png"},
                {"slug": "c", "image_source": base + "/nope"},
                {"slug": "d"}]))
            ok, failed = fetch.fetch_all(pf, imgs)
            self.assertEqual((ok, failed), (["a", "b"], ["c"]))
            saved = json.loads(pf.read_text())
            self.assertEqual(saved[0]["photo"], "a.jpg")
            self.assertEqual(Image.open(imgs / "a.jpg").size, (40, 60))
            self.assertNotIn("photo", saved[3])
        srv.shutdown()

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
