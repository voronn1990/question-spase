"""Public resume and router regressions for persisted/current-graph invalidation."""
from __future__ import annotations
import importlib.util
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
PACKAGE=Path(sys.argv.pop(1)) if len(sys.argv)>1 and Path(sys.argv[1]).is_dir() else (HERE if (HERE/'scripts/qs.py').is_file() else HERE/'candidate/question-space')
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
fixture=load('resume_regression_fixture',PACKAGE/'tests/test_qs.py')
router_fixture=load('router_regression_fixture',PACKAGE/'tests/test_orchestration.py')

class ResumeInvalidationRegressionTest(unittest.TestCase):
    def setUp(self):self.case=fixture.QsTest();self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def test_plain_resume_preserves_persisted_stale_results(self):
        c=self.case
        evidence=c.common('E-001','P-01','/root/evidence');evidence['changed']='UNCHANGED'
        c.register_and_accept('evidence','P-01','EVIDENCE','evidence/E-001.yaml',evidence,'/root/evidence')
        synthesis=c.common('S-STALE','OP-SYNTHESIS','/root/synthesis')
        synthesis['dependency_edges']=[{'from_id':'E-001','to_id':'S-STALE'}]
        c.register_and_accept('stale','OP-SYNTHESIS','SYNTHESIS','synthesis/S-STALE.yaml',synthesis,'/root/synthesis')
        changed=c.call('resume','--root',str(c.workspace),'--run-id','R1','--changed','E-001')
        resumed=c.call('resume','--root',str(c.workspace),'--run-id','R1')
        self.assertEqual(changed['stale_result_ids'],['E-001','S-STALE'])
        self.assertEqual(resumed['stale_result_ids'],changed['stale_result_ids'])
        states={item['assignment_id']:item['status'] for item in resumed['tasks']}
        self.assertEqual(states,{'A-evidence':'STALE','A-stale':'STALE'})
        self.assertEqual(fixture.qs.load_yaml(c.run/'state/current.yaml')['stale_result_ids'],resumed['stale_result_ids'])
    def test_resume_hash_bound_accepted_dependency_invalidates_only_its_consumer(self):
        c=self.case
        evidence=c.common('E-HASH','P-01','/root/evidence');evidence['changed']='UNCHANGED'
        accepted=c.register_and_accept('E-HASH','P-01','EVIDENCE','evidence/E-HASH.yaml',evidence,'/root/evidence')
        for rid,dependencies in (('D-DEPENDENT',[accepted['content_hash']]),('D-INDEPENDENT',[])):
            doc=c.common(rid,'OP-SYNTHESIS','/root/synthesis')
            assignment={'assignment_id':'ASG-'+rid,'attempt_id':'TRY-1','role_id':'OP-SYNTHESIS','agent_id':'/root/synthesis','input_snapshot':c.init['input_snapshot'],'artifact_type':'DECISION','output_path':'decisions/'+rid+'.yaml','dependency_hashes':dependencies,'result_id':rid}
            doc.update({'assignment_id':assignment['assignment_id'],'attempt_id':'TRY-1','artifact_type':'DECISION'})
            ap=c.base/(rid+'.assignment.yaml');rp=c.base/(rid+'.result.yaml')
            fixture.put(ap,assignment);fixture.put(rp,doc)
            c.call('assign','--root',str(c.workspace),'--run-id','R1','--assignment',str(ap))
            c.call('accept','--root',str(c.workspace),'--run-id','R1','--assignment',str(ap),'--result',str(rp))
        resumed=c.call('resume','--root',str(c.workspace),'--run-id','R1','--changed','E-HASH')
        self.assertEqual(resumed['stale_result_ids'],['D-DEPENDENT','E-HASH'])
        states={item['result_id']:item['status'] for item in resumed['tasks']}
        self.assertEqual(states['D-DEPENDENT'],'STALE');self.assertEqual(states['D-INDEPENDENT'],'ACCEPTED')
    def test_resume_provenance_then_execution_excludes_semantic_relationships(self):
        c=self.case
        for rid in ('E-ORIGIN','E-DERIVED','E-UNRELATED'):
            doc=c.common(rid,'P-01','/root/evidence');doc['changed']='UNCHANGED'
            if rid=='E-DERIVED':doc['source_refs']=['E-ORIGIN']
            c.register_and_accept(rid,'P-01','EVIDENCE','evidence/'+rid+'.yaml',doc,'/root/evidence')
        for rid in ('D-CONSUMER','D-SEMANTIC-ONLY'):
            c.register_and_accept(rid,'OP-SYNTHESIS','DECISION','decisions/'+rid+'.yaml',c.common(rid,'OP-SYNTHESIS','/root/synthesis'),'/root/synthesis')
        graph=c.common('S-GRAPH','OP-SYNTHESIS','/root/synthesis')
        graph.update({'evidence_provenance_edges':[{'from_id':'E-ORIGIN','to_id':'E-DERIVED'}],
            'execution_edges':[{'from_type':'E','from_id':'E-DERIVED','to_type':'Q','to_id':'Q-001'},{'from_type':'Q','from_id':'Q-001','to_type':'D','to_id':'D-CONSUMER'}],
            'semantic_edges':[{'from_type':'E','from_id':'E-ORIGIN','to_type':'D','to_id':'D-SEMANTIC-ONLY'}]})
        c.register_and_accept('graph','OP-SYNTHESIS','SYNTHESIS','synthesis/S-GRAPH.yaml',graph,'/root/synthesis')
        changed=c.call('resume','--root',str(c.workspace),'--run-id','R1','--changed','E-ORIGIN')
        self.assertEqual(changed['stale_result_ids'],['D-CONSUMER','E-DERIVED','E-ORIGIN','Q-001'])
        states={item['result_id']:item['status'] for item in changed['tasks']}
        self.assertEqual(states['E-DERIVED'],'STALE');self.assertEqual(states['D-CONSUMER'],'STALE')
        self.assertEqual(states['D-SEMANTIC-ONLY'],'ACCEPTED');self.assertEqual(states['E-UNRELATED'],'ACCEPTED')
        durable=fixture.qs.load_yaml(c.run/'state/current.yaml')['stale_result_ids']
        self.assertEqual(durable,['D-CONSUMER','E-DERIVED','E-ORIGIN'])
        self.assertNotIn('Q-001',durable)
        resumed=c.call('resume','--root',str(c.workspace),'--run-id','R1')
        self.assertEqual(resumed['stale_result_ids'],changed['stale_result_ids'])

class RouterCurrentGraphRegressionTest(unittest.TestCase):
    def setUp(self):self.model=router_fixture.CompleteOrchestration()
    def test_router_provenance_then_execution_excludes_semantic_relationships(self):
        model=self.model
        model.docs['RES-BRANCH'].pop('dependency_edges')
        model.docs['RES-BRANCH'].update({'evidence_provenance_edges':[{'from_id':'E-001','to_id':'E-DERIVED'}],
            'execution_edges':[{'from_type':'E','from_id':'E-DERIVED','to_type':'D','to_id':'D-CONSUMER'}],
            'semantic_edges':[{'from_type':'E','from_id':'E-001','to_type':'OPERATOR','to_id':'OP-BRANCH'}]})
        model.docs['RES-DERIVED']={**model.docs['RES-E'],'id':'E-DERIVED','source_refs':['E-001']}
        model.results['RES-DERIVED']=model.result('hash-derived','EVIDENCE','ASG-DERIVED')
        model.approved['evidence_applicability']['E-DERIVED']=[]
        model.assignments['DERIVED\0A1']=model.registered(model.assignment('ASG-DERIVED','RES-DERIVED','P-04','EVIDENCE'),'ACCEPTED')
        model.results['RES-CONSUMER']=model.result('hash-consumer','DECISION','ASG-CONSUMER')
        model.docs['RES-CONSUMER']={'id':'D-CONSUMER','artifact_type':'DECISION'}
        model.assignments['CONSUMER\0A1']=model.registered(model.assignment('ASG-CONSUMER','RES-CONSUMER','P-01','DECISION',assigned={'questions':[],'requirements':[],'decisions':['D-CONSUMER'],'evidence':[]}),'ACCEPTED')
        result=model.validate(['E-001'])
        self.assertEqual(result['errors'],[])
        self.assertTrue({'E-DERIVED','RES-DERIVED','D-CONSUMER','RES-CONSUMER'}.issubset(result['stale_ids']))
        self.assertNotIn('RES-BRANCH',result['stale_ids'])
        self.assertEqual(next(item['state'] for item in result['task_states'] if item['assignment_id']=='ASG-CONSUMER'),'STALE')
    def test_router_keeps_persisted_stale_result_and_waits_its_consumers(self):
        m=self.model
        result=router_fixture.validate_orchestration(m.bank,m.approved,m.assignments,m.results,m.docs,[],None,None,['RES-Q1'])
        self.assertEqual(result['errors'],[])
        states={item['assignment_id']:item['state'] for item in result['task_states']}
        self.assertEqual(states['ASG-Q1'],'STALE');self.assertEqual(states['ASG-D'],'STALE')
        self.assertEqual(states['ASG-SUBJECT'],'STALE');self.assertEqual(states['ASG-SYSTEM'],'STALE')
        ready={item for group in result['ready_groups'] for item in group['assignment_ids']}
        self.assertNotIn('ASG-SUBJECT',ready);self.assertNotIn('ASG-SYSTEM',ready)
        self.assertEqual(states['ASG-BRANCH'],'ACCEPTED')

if __name__=='__main__':unittest.main(verbosity=2)
