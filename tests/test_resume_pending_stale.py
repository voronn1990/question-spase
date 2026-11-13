"""Public CLI regression for pending exact-hash consumers of stale results."""
from __future__ import annotations
import importlib.util
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
PACKAGE=Path(sys.argv.pop(1)) if len(sys.argv)>1 and Path(sys.argv[1]).is_dir() else HERE
spec=importlib.util.spec_from_file_location('pending_stale_fixture',PACKAGE/'tests/test_qs.py')
fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)

class PendingStaleConsumerTest(unittest.TestCase):
    def test_pending_hash_consumer_waits_after_changed_and_plain_resume(self):
        c=fixture.QsTest();c.setUp()
        try:
            evidence=c.common('E-STALE','P-01','/root/evidence');evidence['changed']='UNCHANGED'
            accepted=c.register_and_accept('e','P-01','EVIDENCE','evidence/E-STALE.yaml',evidence,'/root/evidence')
            for label,dependencies,launch in (
                ('DEPENDENT',[accepted['content_hash']],'READY_TO_DISPATCH'),
                ('UNKNOWN',[accepted['content_hash']],'DISPATCH_UNKNOWN'),
                ('INDEPENDENT',[],'READY_TO_DISPATCH'),
            ):
                assignment={'assignment_id':'A-'+label,'attempt_id':'TRY-1','role_id':'OP-SYNTHESIS','agent_id':'/root/'+label.lower(),'input_snapshot':c.init['input_snapshot'],'artifact_type':'DECISION','output_path':'decisions/D-'+label+'.yaml','dependency_hashes':dependencies,'result_id':'D-'+label,'group_id':'1','launch_state':launch}
                path=c.base/(label+'.assignment.yaml');fixture.put(path,assignment)
                c.call('assign','--root',str(c.workspace),'--run-id','R1','--assignment',str(path))
            changed=c.call('resume','--root',str(c.workspace),'--run-id','R1','--changed','E-STALE')
            durable_after_change=(c.run/'state/current.yaml').read_bytes()
            plain=c.call('resume','--root',str(c.workspace),'--run-id','R1')
            for result in (changed,plain):
                states={item['assignment_id']:item['status'] for item in result['tasks']}
                self.assertEqual(states['A-DEPENDENT'],'WAITING')
                self.assertEqual(states['A-UNKNOWN'],'DISPATCH_UNKNOWN')
                self.assertEqual(states['A-INDEPENDENT'],'READY')
                self.assertEqual(result['ready_groups'],[{'group_id':'1','assignment_ids':['A-INDEPENDENT']}])
                self.assertEqual(result['stale_result_ids'],['E-STALE'])
            self.assertEqual((c.run/'state/current.yaml').read_bytes(),durable_after_change)
            self.assertEqual(fixture.qs.load_yaml(c.run/'state/current.yaml')['stale_result_ids'],['E-STALE'])
        finally:c.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
