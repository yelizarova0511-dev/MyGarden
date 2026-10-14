import unittest
from main import app


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Добро пожаловать в My Garden".encode(), response.data)

    def test_about_page(self):
        self.assertEqual(self.client.get("/about").status_code, 200)

    def test_login_page(self):
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_register_page(self):
        self.assertEqual(self.client.get("/register").status_code, 200)

    def test_not_found_page(self):
        self.assertEqual(self.client.get("/missing-page").status_code, 404)


if __name__ == "__main__":
    unittest.main()
