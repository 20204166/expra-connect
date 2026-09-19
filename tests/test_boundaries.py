import unittest


class BoundaryTests(unittest.TestCase):
    def test_public_package_does_not_import_application_modules(self) -> None:
        import expra_connect

        names = set(expra_connect.__dict__)
        self.assertFalse(any("tk" in name.lower() for name in names))
