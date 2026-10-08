"""The native client uses the interpreter's SQLite ABI on Windows."""
from pathlib import Path
import sys
import unittest
from bindscope import native_sqlite


class LibrarySelectionTests(unittest.TestCase):
    def test_windows_interpreter_library(self):
        if sys.platform == 'win32':
            bundled = Path(sys.base_prefix) / 'DLLs' / 'sqlite3.dll'
            if bundled.is_file():
                self.assertEqual(Path(native_sqlite._libname), bundled)
        # This exercises the selected library rather than merely testing a path.
        with native_sqlite.Connection() as connection:
            self.assertEqual(connection.query('SELECT 42')[2], (((1, 42),),))


if __name__ == '__main__':
    unittest.main()
