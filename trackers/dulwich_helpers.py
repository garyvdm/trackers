import stat
from contextlib import suppress
from time import time, timezone

from dulwich.errors import NotTreeError
from dulwich.objects import Blob, Commit, Tree
from dulwich.objectspec import parse_tree


class TreeReader(object):

    def __init__(self, repo, treeish='HEAD', encoding="UTF-8"):
        self.repo = repo
        self.tree = parse_tree(repo, treeish)
        self.lookup_obj = repo.__getitem__
        self.encoding = encoding

    def lookup(self, path):
        return self.tree.lookup_path(self.lookup_obj, path.encode(self.encoding))

    def get(self, path):
        _, sha = self.tree.lookup_path(self.lookup_obj, path.encode(self.encoding))
        return self.lookup_obj(sha)

    def tree_items(self, path):
        tree = self.get(path)
        if not isinstance(tree, Tree):
            raise NotTreeError(path)
        for item in tree:
            yield item.decode(self.encoding)

    def exists(self, path):
        try:
            self.lookup(path)
        except KeyError:
            return False
        else:
            return True


class TreeWriter(TreeReader):

    def __init__(self, repo, branch=b'HEAD', encoding="UTF-8"):
        self.repo = repo
        self.encoding = encoding
        self.branch = branch
        self._reset()

    def _reset(self):
        self.org_commit_id = self.repo.refs[self.branch]
        self.tree = parse_tree(self.repo, self.org_commit_id)
        self.changed_objects = {}

    def lookup_obj(self, sha):
        try:
            return self.changed_objects[sha]
        except KeyError:
            return self.repo[sha]

    def set(self, path, obj, mode):
        path_items = path.encode(self.encoding).split(b'/')
        sub_tree = self.tree
        trees = [sub_tree]
        for name in path_items[:-1]:
            try:
                _, sub_tree_sha = sub_tree[name]
            except KeyError:
                sub_tree = Tree()
            else:
                sub_tree = self.lookup_obj(sub_tree_sha)
            trees.append(sub_tree)

        for sub_tree, name in reversed(list(zip(trees, path_items))):
            with suppress(KeyError):
                del self.changed_objects[sub_tree.id]

            if obj is None:
                del sub_tree[name]
            else:
                obj_id = obj.id
                self.changed_objects[obj_id] = obj
                sub_tree[name] = (mode, obj_id)

            if len(sub_tree) == 0:
                obj = None
                mode = None
            else:
                obj = sub_tree
                mode = stat.S_IFDIR

        self.changed_objects[sub_tree.id] = sub_tree

    def set_data(self, path, data, mode=stat.S_IFREG | 0o644):
        obj = Blob()
        obj.data = data
        self.set(path, obj, mode)

    def remove(self, path):
        self.set(path, None, None)

    def commit(self, message, author=None):
        commit = Commit()
        commit.tree = self.tree.id
        if author is None:
            author = self.repo._get_user_identity()
        else:
            author = author.encode(self.encoding)
        commit.author = commit.committer = author
        commit.commit_time = commit.author_time = int(time())
        tz = timezone
        commit.commit_timezone = commit.author_timezone = tz
        commit.message = message.encode(self.encoding)
        commit.encoding = self.encoding.encode('ascii')
        commit.parents = [self.org_commit_id]
        commit_id = commit.id
        self.changed_objects[commit_id] = commit
        self.repo.object_store.add_objects([(obj, None) for obj in self.changed_objects.values()])
        self.repo.refs.set_if_equals(self.branch, self.org_commit_id, commit_id)
