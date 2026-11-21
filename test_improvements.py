import ast
import os
import gzip
import json
import sys
from enum import Enum
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import operand_proofs
import profile_baseline
import verification
import attribute_dependencies
import statement_scope
import semantic_proofs
from verification_plan import Planner
import entry

class ImprovementTests(unittest.TestCase):
    def test_compiler_rename_checks_arguments_values_and_reflection(self):
        def agrees(before,after):
            return semantic_proofs.code_equal(compile(before,'x.py','exec'),compile(after,'x.py','exec'))
        old='def f(value):\n local = value + 1\n return local\n'
        new='def f(value):\n renamed = value + 1\n return renamed\n'
        self.assertTrue(agrees(old,new))
        self.assertFalse(agrees(old,new.replace('value + 1','value + 2')))
        self.assertFalse(agrees('def f(a):\n return a\n','def f(b):\n return b\n'))
        self.assertFalse(agrees('def f():\n a = 1\n return locals()\n','def f():\n b = 1\n return locals()\n'))
        self.assertFalse(agrees('def f():\n a = False\n return a\n','def f():\n b = 0\n return b\n'))
        self.assertFalse(agrees('def f(flag):\n if flag: local=1\n return local\n','def f(flag):\n if flag: renamed=1\n return renamed\n'))
    def test_compiler_rename_patch_requires_matching_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'sqlglot').mkdir()
            (root/'sqlglot/x.py').write_text('def f():\n a = 1\n return a\n')
            diff='diff --git a/sqlglot/x.py b/sqlglot/x.py\n--- a/sqlglot/x.py\n+++ b/sqlglot/x.py\n@@ -1,3 +1,3 @@\n def f():\n- a = 1\n- return a\n+ b = 1\n+ return b\n'
            self.assertTrue(semantic_proofs.equivalent_patch(diff,root))
            self.assertFalse(semantic_proofs.equivalent_patch(diff.replace('+ b = 1','+ b = 2'),root))
    def test_profile_requires_pinned_successful_matching_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            payload={'pin':'eb11aec4be8cb2122afe713a4816662c3f0fb2fa','pytest_exit':0,'python':sys.version,'relationship_version':2}
            def write():
                with gzip.open(root/'baseline-profile.json.gz','wt') as file:
                    json.dump(payload,file)
            write()
            self.assertIsNotNone(operand_proofs.read_profile(root))
            payload['pytest_exit']=1
            write()
            self.assertIsNone(operand_proofs.read_profile(root))
            payload['pytest_exit']=0
            payload['python']='0.0.0'
            write()
            self.assertIsNone(operand_proofs.read_profile(root))
    @unittest.skipUnless(hasattr(os,'fork'),'Requires Linux fork isolation')
    def test_isolated_verification_does_not_share_failed_test_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'sqlglot').mkdir()
            (root/'tests').mkdir()
            (root/'sqlglot/__init__.py').write_text('VALUE = 0\n')
            (root/'tests/__init__.py').write_text('')
            (root/'tests/test_state.py').write_text('import unittest\nSTATE=[]\nclass T(unittest.TestCase):\n def test_a(self):\n  STATE.append(1)\n  self.fail("failed after mutation")\n def test_b(self): self.assertEqual(STATE,[])\n')
            names=['tests/test_state.py::T::test_a','tests/test_state.py::T::test_b']
            tests={name:{'id':name,'number':i,'file':'tests/test_state.py','seconds':0.01} for i,name in enumerate(names)}
            diff='diff --git a/sqlglot/__init__.py b/sqlglot/__init__.py\n--- a/sqlglot/__init__.py\n+++ b/sqlglot/__init__.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 2\n'
            selected,info=verification.refine(diff,set(names),tests,root,force=True,wall_budget=3)
            self.assertEqual(selected,{names[0]},info)
    def test_value_relationships_and_custom_equality(self):
        class TextChoice(str,Enum):
            A='a'
            B='b'
        self.assertEqual(profile_baseline.relationship(TextChoice.A,TextChoice.A),8)
        self.assertEqual(profile_baseline.relationship(TextChoice.A,TextChoice.B),1)
        class CustomText(str):
            def __eq__(self,other):
                raise AssertionError('Do not invoke custom string equality')
        self.assertEqual(profile_baseline.relationship(CustomText('a'),TextChoice.A),16)
        class Custom:
            def __eq__(self,other):
                raise AssertionError('Do not call arbitrary equality')
        self.assertEqual(profile_baseline.relationship(None,None),8)
        self.assertEqual(profile_baseline.relationship(Custom(),None),16)
        class CustomNotEqual:
            def __ne__(self,other):
                raise AssertionError('Do not call arbitrary inequality')
        self.assertEqual(profile_baseline.relationship(CustomNotEqual(),None),16)
        self.assertTrue(operand_proofs.same_result(9,'Is','Eq'))
        self.assertFalse(operand_proofs.same_result(2,'Is','Eq'))
        self.assertFalse(operand_proofs.same_result(16,'Is','Eq'))
        self.assertFalse(operand_proofs.same_result(4,'Is','Eq'))
    def test_only_isolated_operator_edits_are_eligible(self):
        before=ast.parse('x = value is None')
        after=ast.parse('x = value == None')
        self.assertEqual(len(operand_proofs.comparison_edits(before,after,'a.py')),1)
        self.assertIsNone(operand_proofs.comparison_edits(before,ast.parse('x = other == None'),'a.py'))
    def test_class_setting_guards(self):
        old=ast.parse('class Settings:\n OFFSET = -1\n')
        new=ast.parse('class Settings:\n OFFSET = -2\n')
        self.assertEqual(attribute_dependencies.isolated_scalar_setting(old,new,2),'OFFSET')
        self.assertIsNone(attribute_dependencies.isolated_scalar_setting(old,ast.parse('@decorator\nclass Settings:\n OFFSET = -2\n'),2))
        enum=ast.parse('class Settings(IntEnum):\n OFFSET = 1\n')
        self.assertIsNone(attribute_dependencies.isolated_scalar_setting(enum,ast.parse('class Settings(IntEnum):\n OFFSET = 2\n'),2))
    def test_statement_scope_preserves_guards_and_rejects_dedent(self):
        old=ast.parse('def f(x):\n if x:\n  return 1\n return 2\n')
        same=ast.parse('def f(x):\n if x:\n  return 3\n return 2\n')
        self.assertEqual(statement_scope.execution_span(old,same,old.body[0],3).lineno,3)
        guard=ast.parse('def f(x):\n if not x:\n  return 1\n return 2\n')
        span=statement_scope.execution_span(old,guard,old.body[0],2)
        self.assertIsInstance(span,ast.If)
        moved=ast.parse('def f(x):\n if x:\n  pass\n return 1\n return 2\n')
        self.assertIs(statement_scope.execution_span(old,moved,old.body[0],3),old.body[0])
        self.assertFalse(statement_scope.initialization_changed(old,same))
        escaped=ast.parse('def f(x):\n return 2\nside_effect()\n')
        self.assertTrue(statement_scope.initialization_changed(old,escaped))
    def test_verification_planner_accounts_for_expensive_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'fast.py').write_text('import unittest\n')
            (root/'slow.py').write_text('import pandas\n')
            planner=Planner(root)
            fast={'a':{'id':'a','file':'fast.py','number':0,'seconds':0.02}}
            slow={'b':{'id':'b','file':'slow.py','number':0,'seconds':0.02}}
            self.assertEqual(planner.choose({'a'},fast,entry.schedule),['a'])
            self.assertEqual(planner.choose({'b'},slow,entry.schedule),[])
    def test_real_worker_keeps_failed_test_and_drops_passed_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'sqlglot').mkdir()
            (root/'tests').mkdir()
            (root/'sqlglot/__init__.py').write_text('VALUE = 0\n')
            (root/'tests/__init__.py').write_text('')
            (root/'tests/test_good.py').write_text('import unittest\nfrom sqlglot import VALUE\nclass Good(unittest.TestCase):\n def test_a(self): self.assertEqual(VALUE,2)\n')
            (root/'tests/test_small.py').write_text('import unittest\nfrom sqlglot import VALUE\nclass Small(unittest.TestCase):\n def test_b(self): self.assertEqual(VALUE,0)\n')
            names=['tests/test_good.py::Good::test_a','tests/test_small.py::Small::test_b']
            tests={name:{'id':name,'number':i,'file':name.split('::')[0],'seconds':0.01} for i,name in enumerate(names)}
            text='diff --git a/sqlglot/__init__.py b/sqlglot/__init__.py\n--- a/sqlglot/__init__.py\n+++ b/sqlglot/__init__.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 2\n'
            selected,info=verification.refine(text,set(names),tests,root,wall_budget=3,force=True)
            self.assertEqual(selected,{names[1]},info)
    def test_subtest_failure_stops_method_and_cleanup_failure_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'sqlglot').mkdir()
            (root/'tests').mkdir()
            (root/'sqlglot/__init__.py').write_text('VALUE = 0\n')
            (root/'tests/__init__.py').write_text('')
            module='import unittest\nfrom pathlib import Path\nclass Small(unittest.TestCase):\n def test_a(self):\n  with self.subTest(case=1): self.assertEqual(1,2)\n  Path("continued").touch()\n def test_b(self): self.assertTrue(True)\n'
            (root/'tests/test_small.py').write_text(module)
            names=['tests/test_small.py::Small::test_a','tests/test_small.py::Small::test_b']
            tests={name:{'id':name,'number':i,'file':'tests/test_small.py','seconds':0.01} for i,name in enumerate(names)}
            text='diff --git a/sqlglot/__init__.py b/sqlglot/__init__.py\n--- a/sqlglot/__init__.py\n+++ b/sqlglot/__init__.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 2\n'
            selected,info=verification.refine(text,set(names),tests,root,wall_budget=3)
            self.assertEqual(selected,{names[0]} if hasattr(os,'fork') else set(names),info)
            self.assertFalse((verification.OVERLAYS[str(root)][1]/'continued').exists())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'sqlglot').mkdir()
            (root/'tests').mkdir()
            (root/'sqlglot/__init__.py').write_text('VALUE = 0\n')
            (root/'tests/__init__.py').write_text('')
            (root/'tests/test_small.py').write_text('import unittest\nclass Small(unittest.TestCase):\n @classmethod\n def tearDownClass(cls): raise RuntimeError("cleanup")\n def test_a(self): self.assertTrue(True)\n')
            name='tests/test_small.py::Small::test_a'
            tests={name:{'id':name,'number':0,'file':'tests/test_small.py','seconds':0.01}}
            selected,info=verification.refine(text,{name},tests,root,wall_budget=3)
            self.assertEqual(selected,{name},info)

if __name__=='__main__':
    unittest.main()
