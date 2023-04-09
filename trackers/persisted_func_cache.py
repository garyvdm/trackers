import logging

import msgpack


class PersistedFuncCache(object):
    def __init__(self, path, func=None):
        self.func = func
        self.path = path
        self.load()
        self.unwritten_cache_items = []
        self.logger = logging.getLogger(f"persisted_func_cache.{path}")

    def load(self):
        self.cache = {}
        try:
            with open(self.path, "rb") as f:
                unpacker = msgpack.Unpacker(f, use_list=False, raw=False)

                while True:
                    try:
                        items = unpacker.unpack()
                        self.cache.update(items)
                    except msgpack.exceptions.OutOfData:
                        break
        except FileNotFoundError:
            open(self.path, "wb").close()

    def __call__(self, *args, **kwargs):
        key = self.key(*args, **kwargs)
        try:
            packed = self.cache[key]
        except KeyError:
            value = self.func(*args, **kwargs)
            packed = self.pack(value)
            self.cache[key] = packed
            self.unwritten_cache_items.append((key, packed))
            if len(self.unwritten_cache_items) >= 10:
                self.write_unwritten()
            return value
        else:
            return self.unpack(packed)

    def write_unwritten(self):
        try:
            items = self.unwritten_cache_items
            self.unwritten_cache_items = []
            with open(self.path, "ab") as f:
                msgpack.pack(items, f)
        except Exception:
            self.logger.exception("Error writing unwritten: ")

    def key(self, *args, **kwargs):
        return args, tuple(sorted(kwargs.items()))

    def pack(self, result):
        return result

    def unpack(self, packed):
        return packed
