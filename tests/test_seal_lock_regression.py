"""Seal publication is serialized before validation and before release writes."""
from __future__ import annotations
import importlib.util
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
PACKAGE=Path(sys.argv.pop(1)) if len(sys.argv)>1 and Path(sys.argv[1]).is_dir() else (HERE if (HERE/'scripts/qs.py').is_file() else HERE/'candidate/question-space')
spec=importlib.util.spec_from_file_location('seal_lock_regression_fixture',PACKAGE/'tests/test_qs.py')
fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)

class Complete(Exception):pass
class SealLockRegressionTest(unittest.TestCase):
    def test_busy_coordinator_lock_prevents_new_or_existing_release_writes(self):
        outer=self
        class Case(fixture.QsTest):
            def invoke(self,args):
                replies=[];old_emit=fixture.qs.emit
                fixture.qs.emit=lambda value,stream=None:replies.append(value)
                try:rc=fixture.qs.main(list(args))
                finally:fixture.qs.emit=old_emit
                return rc,replies[-1]
            def call(self,*args,ok=True):
                if args[0]=='seal' and args[-1]=='finalize':
                    paths=[self.run/'release.yaml',self.run/'releases/REL-2.yaml']
                    current_path=self.run/'state/current.yaml'
                    before=current_path.read_bytes()
                    outer.assertFalse(any(path.exists() for path in paths))
                    with fixture.qs.lock(self.run):rc,data=self.invoke(args)
                    outer.assertEqual((rc,data.get('error')),(2,'coordinator_lock_busy'))
                    outer.assertFalse(any(path.exists() for path in paths),'rejected seal created published release files')
                    outer.assertEqual(current_path.read_bytes(),before)
                    # Once the other writer releases the lock the same exact
                    # validated lifecycle must finalize and verify normally.
                    rc,data=self.invoke(args)
                    outer.assertEqual((rc,data.get('status')),(0,'SEALED'),data)
                    rc,verified=self.invoke(('verify','--root',str(self.workspace),'--run-id',self.run.name))
                    outer.assertEqual((rc,verified.get('status')),(0,'PASS'),verified)
                    published={path:path.read_bytes() for path in paths}
                    sealed_current=current_path.read_bytes()
                    with fixture.qs.lock(self.run):rc,data=self.invoke(args)
                    outer.assertEqual((rc,data.get('error')),(2,'coordinator_lock_busy'))
                    outer.assertEqual(current_path.read_bytes(),sealed_current)
                    outer.assertEqual({path:path.read_bytes() for path in paths},published)
                    # Reusing the same release identity keeps archive bytes.
                    rc,data=self.invoke(args)
                    outer.assertEqual((rc,data.get('status')),(0,'SEALED'),data)
                    outer.assertEqual({path:path.read_bytes() for path in paths},published)
                    rc,verified=self.invoke(('verify','--root',str(self.workspace),'--run-id',self.run.name))
                    outer.assertEqual((rc,verified.get('status')),(0,'PASS'),verified)
                    raise Complete()
                rc,data=self.invoke(args)
                if ok:outer.assertEqual(rc,0,data)
                else:outer.assertNotEqual(rc,0,data)
                return data
        case=Case();case.setUp()
        try:
            with self.assertRaises(Complete):case.test_public_lifecycle_allows_renamed_administrative_gate6_review_path()
        finally:case.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
