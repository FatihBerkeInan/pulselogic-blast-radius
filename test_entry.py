import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import entry

class EntryTests(unittest.TestCase):
    def test_multiple_hunks_and_insertion(self):
        text = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n x = 1\n+y = 3\n z = 2\n@@ -4 +5 @@\n-old = 4\n+new = 5\n'
        change = entry.parse_patch(text)[0]
        self.assertEqual(entry.reconstruct('x = 1\nz = 2\n\nold = 4\n',change['hunks']),
                         'x = 1\ny = 3\nz = 2\n\nnew = 5\n')

    def test_context_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            entry.reconstruct('x = 1\n', [[1,['-x = 2','+x = 3']]])

    def test_worker_cost_and_uniqueness(self):
        tests = [{'number':i, 'seconds':0.01} for i in range(40)]
        self.assertEqual(len(entry.schedule(tests)),1)
        self.assertEqual(sorted(sum(entry.schedule(tests),[])),list(range(40)))
        long = [{'number':i,'seconds':20} for i in range(8)]
        self.assertEqual(len(entry.schedule(long)),8)
        self.assertEqual(entry.schedule([]),[])

    def test_function_dependencies_and_ast_equivalence(self):
        tests = [{'id':'a','file':'tests/test_a.py','number':0,'seconds':0.1},
                 {'id':'b','file':'tests/test_b.py','number':1,'seconds':0.1}]
        maps = {'lines':{'m.py':{'2':['a'],'4':['b']}}, 'files':{}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'m.py').write_text('def f():\n    return 1\ndef g():\n    return 2\n')
            with patch.object(entry,'SOURCE',root):
                selector = entry.Selector(tests,maps)
                prefix='diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n'
                self.assertEqual(selector.select(prefix+'@@ -2 +2 @@\n-    return 1\n+    return 3\n'),{'a'})
                self.assertEqual(selector.select(prefix+'@@ -2 +2 @@\n-    return 1\n+    return 1 # harmless\n'),set())
                self.assertEqual(selector.select(prefix+'@@ -2 +2 @@\n-    return 1\n+    return (\n'),{'a','b'})

    def test_encoding_comment_can_change_runtime(self):
        tests = [{'id':'a','file':'tests/test_a.py','number':0,'seconds':0.1}]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'m.py').write_text("# coding: utf-8\nx = 'Ã©'\n",encoding='utf-8')
            with patch.object(entry,'SOURCE',root):
                selector=entry.Selector(tests,{'lines':{},'files':{}})
                text='diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-# coding: utf-8\n+# coding: ascii\n'
                self.assertEqual(selector.select(text),{'a'})

    def test_fixture_loaded_by_class_setup_affects_entire_class(self):
        first='tests/test_a.py::A::test_first'
        second='tests/test_a.py::A::test_second'
        other='tests/test_b.py::B::test_other'
        tests=[{'id':node,'file':node.split('::')[0],'number':i,'seconds':0.1}
               for i,node in enumerate([first,second,other])]
        maps={'lines':{'tests/test_a.py':{'3':[first]}},
              'files':{'fixture.sql':[first]}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'tests').mkdir()
            (root/'fixture.sql').write_text('SELECT 1;\n')
            (root/'tests/test_a.py').write_text('class A:\n    def setUpClass(cls):\n        pass\n')
            with patch.object(entry,'SOURCE',root):
                selector=entry.Selector(tests,maps)
                text='diff --git a/fixture.sql b/fixture.sql\n--- a/fixture.sql\n+++ b/fixture.sql\n@@ -1 +1 @@\n-SELECT 1;\n+SELECT 2;\n'
                self.assertEqual(selector.select(text),{first,second})

if __name__ == '__main__':
    unittest.main()
