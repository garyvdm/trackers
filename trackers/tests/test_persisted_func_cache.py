import os.path
import tempfile
import unittest
import unittest.mock

from trackers.persisted_func_cache import PersistedFuncCache


class TestPersistedFuncCache(unittest.TestCase):
    def test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cache")
            cache = PersistedFuncCache(path)

            cache.func = func = unittest.mock.Mock(side_effect=lambda x: x + 1)
            cache.key = lambda x: x
            cache.pack = lambda x: str(x)
            cache.unpack = lambda packed: int(packed)

            self.assertEqual(cache(1), 2)
            func.assert_called_once_with(1)

            self.assertEqual(cache(1), 2)
            func.assert_called_once_with(1)  # second time should not be called again.

            cache.write_unwritten()

            loaded_cache = PersistedFuncCache(path)
            loaded_cache.func = loaded_func = unittest.mock.Mock(side_effect=lambda x: x + 1)
            loaded_cache.key = lambda x: x
            loaded_cache.pack = lambda x: str(x)
            loaded_cache.unpack = lambda packed: int(packed)

            self.assertEqual(loaded_cache(1), 2)
            loaded_func.assert_not_called()
