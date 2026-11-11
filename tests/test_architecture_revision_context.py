"""Bounded structural regressions for contexts during immutable PLAN repair."""
from __future__ import annotations
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_architecture_integration import CompleteArchitectureModel, bind_architecture, model
from test_iceberg_contracts import _refresh_hash_bindings
from test_architecture import graph_for_context
from test_qs import qs


class ArchitectureRevisionContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.fixture=CompleteArchitectureModel(Path(self.tmp.name))
        self.prepare_public_fixture()

    def tearDown(self):self.tmp.cleanup()

    def register(self,assignment,status='ASSIGNED'):
        aid=assignment['assignment_id'];attempt=assignment['attempt_id']
        self.state['registered'][aid+'\0'+attempt]={'assignment':assignment,'status':status}
        self.state['current_attempts'][aid]=attempt

    def replacement(self,logical,*,accept=True,separate=False):
        doc=copy.deepcopy(self.fixture.docs[logical]);rid=logical+'-R2'
        aid='ASG-'+(rid if separate else logical);attempt='ATT-1' if separate else 'ATT-2'
        doc.update({'id':rid,'assignment_id':aid,'attempt_id':attempt,'supersedes':logical,'plan_version':'2','revision':'2'})
        doc['identity_result']['plan_version']='2'
        doc['local_execution_graph']['predecessors']=[] if logical=='S-000' else ['S-000-R2']
        doc['local_execution_graph']['next_stages']=['S-001-R2'] if logical=='S-000' else []
        assignment={key:doc[key] for key in ('assignment_id','attempt_id','agent_id','input_snapshot','artifact_type')}
        assignment.update({'role_id':'PLAN-AUTHOR','result_id':rid,'output_path':'plan/'+rid+'.yaml','dependency_hashes':[]})
        if not separate:
            assignment['supersedes_attempt_id']='ATT-1';doc['supersedes_attempt_id']='ATT-1'
        if accept:self.fixture.add_result(rid,'PLAN','PLAN-AUTHOR',doc['agent_id'],assignment['output_path'],doc)
        self.register(assignment,'ACCEPTED' if accept else 'ASSIGNED')
        return rid

    def project(self,**kwargs):
        qs.save_yaml(self.fixture.run/'state/current.yaml',self.current)
        return qs.architecture_projection_for_run(self.fixture.run,self.state,self.current,self.fixture.bank,
            allow_unselected=True,allow_stale=True,**kwargs)

    def assert_incomplete(self,error):
        self.assertEqual(self.project(allow_incomplete=True),{'selection_status':'INCOMPLETE','errors':['architecture:'+error]})
        with self.assertRaisesRegex(qs.QSError,'^'+error+'$'):self.project()

    def test_pending_retry_context_precedes_first_replacement_result(self):
        self.replacement('S-000',accept=False)
        self.assert_incomplete('plan_projection_current_attempt')

    def test_selected_v1_partial_v2_and_pending_retry_have_no_selected_graph(self):
        self.replacement('S-000');self.replacement('S-001',accept=False)
        self.assert_incomplete('plan_projection_current_attempt')

    def test_separate_assignment_descendant_is_incomplete_until_exact_map_selected(self):
        self.replacement('S-000',separate=True)
        self.assert_incomplete('plan_projection_ambiguous_history')
        self.replacement('S-001',separate=True)
        projection={'plan_version':'2','stages':[
            {'logical_stage_id':logical,'result_id':logical+'-R2','sha256':self.fixture.results[logical+'-R2']['content_hash'],
             'assignment_id':'ASG-'+logical+'-R2','attempt_id':'ATT-1'} for logical in ('S-000','S-001')]}
        self.current={**self.current,'current_plan_projection':projection,
                      'current_plan_projection_sha256':qs.sha(qs.canon_yaml(projection))}
        self.assertEqual(self.project()['selection_status'],'SELECTED')

    def test_selected_map_and_result_hash_corruption_remain_fatal(self):
        self.replacement('S-000');self.replacement('S-001',accept=False)
        valid=copy.deepcopy(self.current)
        self.current['current_plan_projection_sha256']='0'*64
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)
        self.current=copy.deepcopy(valid)
        self.current['current_plan_projection']['stages'][0]['sha256']='0'*64
        self.current['current_plan_projection_sha256']=qs.sha(qs.canon_yaml(self.current['current_plan_projection']))
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)
        self.current=valid
        path=self.fixture.run/self.fixture.results['S-000']['path'];path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)

    def test_unregistered_or_unrelated_current_attempt_remains_fatal(self):
        self.replacement('S-000');self.replacement('S-001',accept=False)
        self.state['current_attempts']['ASG-S-001']='UNKNOWN'
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)
        pending=self.state['registered']['ASG-S-001\0ATT-2']['assignment']
        self.state['current_attempts']['ASG-S-001']='ATT-2';pending.pop('supersedes_attempt_id')
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)
        pending['supersedes_attempt_id']='ATT-1';pending['artifact_type']='PRIMARY'
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)

    def test_registration_binding_and_result_fork_remain_fatal(self):
        self.replacement('S-000',separate=True)
        record=self.state['registered']['ASG-S-000-R2\0ATT-1']['assignment'];record['result_id']='S-001'
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)
        record['result_id']='S-000-R2'
        doc=copy.deepcopy(self.fixture.docs['S-000-R2']);doc.update({'id':'S-000-FORK','assignment_id':'ASG-FORK'})
        self.fixture.add_result('S-000-FORK','PLAN','PLAN-AUTHOR',doc['agent_id'],'plan/S-000-FORK.yaml',doc)
        entry=self.fixture.results['S-000-FORK'];assignment={key:entry[key] for key in ('assignment_id','attempt_id','role_id','agent_id','input_snapshot','artifact_type','result_id')}
        assignment.update({'output_path':entry['path'],'dependency_hashes':[]});self.register(assignment,'ACCEPTED')
        with self.assertRaises(qs.QSError):self.project(allow_incomplete=True)

    def prepare_public_fixture(self):
        # Adapt accepted structural model bytes before testing real public CLI
        # registration/context. This is not a native GOAL/BANK admission proof.
        f=self.fixture
        snapshot={'schema_version':qs.SCHEMA,'run_id':'RUN-1','input_mode':'LEGACY_BANK',
                  'methodology_features':['ARCHITECTURE_GRAPH_V1'],
                  'bank_sha256':qs.sha((f.run/'input/bank.yaml').read_bytes()),
                  'requirements_sha256':qs.sha(qs.canon_yaml(f.bank['requirements']))}
        qs.save_yaml(f.run/'input/snapshot.yaml',snapshot);f.snapshot=qs.sha((f.run/'input/snapshot.yaml').read_bytes())
        f.docs['VECTOR-001'].update(G='bounded fixture goal',NOT_G=['outside bounded fixture goal'])
        for rid,doc in f.docs.items():doc['input_snapshot']=f.snapshot;f.results[rid]['input_snapshot']=f.snapshot
        _refresh_hash_bindings(f)
        for stage in ('S-000','S-001'):
            for row in f.docs[stage]['inputs_contracts']['inputs']:
                if row.get('source_id')=='E-001':row['accepted_hash']=f.results['E-001']['content_hash']
            f.sync(stage)
        approval=qs.load_yaml(f.run/'input/vector-approval.yaml')
        approval.update(input_snapshot=f.snapshot,vector_sha256=f.results['VECTOR-001']['content_hash'])
        qs.save_yaml(f.run/'input/vector-approval.yaml',approval)
        context=model.architecture_context(f.run,f.bank,f.results,f.docs,{k:f.docs[k] for k in ('S-000','S-001')},load_yaml=qs.load_yaml,sha=qs.sha)
        f.docs['SYNTHESIS-001']['architecture_graph']=graph_for_context(context);bind_architecture(f)
        self.state={'schema_version':qs.SCHEMA,'run_id':'RUN-1','input_snapshot':f.snapshot,
                    'results':f.results,'registered':{},'current_attempts':{}}
        for rid,entry in f.results.items():
            assignment={key:entry[key] for key in ('assignment_id','attempt_id','role_id','agent_id','input_snapshot','artifact_type','result_id')}
            assignment.update(output_path=entry['path'],dependency_hashes=[]);self.register(assignment,'ACCEPTED')
        _,projection=qs.plan_projection_for_run(f.run,self.state,{})
        self.current={'schema_version':qs.SCHEMA,'run_id':'RUN-1','input_snapshot':f.snapshot,'status':'WORKING',
                      'current_plan_projection':projection,'current_plan_projection_sha256':qs.sha(qs.canon_yaml(projection))}
        qs.save_yaml(f.run/'state/events.yaml',{'events':[]})

    def public_context(self,*,dependency='S-000-R2',worker=False,unrelated=False,hashless=False,forged=False):
        self.replacement('S-000')
        if forged:self.state['registered']['ASG-S-000\0ATT-2']['assignment']['result_id']='UNRELATED'
        f=self.fixture
        qs.save_yaml(f.run/'state/assignments.yaml',self.state);qs.save_yaml(f.run/'state/current.yaml',self.current)
        assignment={'assignment_id':'ASG-FRESH' if worker or unrelated else 'ASG-S-001',
                    'attempt_id':'ATT-1' if worker or unrelated else 'ATT-2',
                    'role_id':'P-01' if worker else 'PLAN-AUTHOR','agent_id':'/root/public-context-fixture',
                    'input_snapshot':f.snapshot,'artifact_type':'PRIMARY' if worker else 'PLAN',
                    'result_id':'PUBLIC-RESULT','output_path':('positions/' if worker else 'plan/')+'PUBLIC-RESULT.yaml',
                    'dependency_hashes':[] if hashless else [f.results[dependency]['content_hash']],
                    'dependency_result_ids':[dependency],'assigned_ids':{'questions':[],'requirements':[]}}
        if not (worker or unrelated):assignment['supersedes_attempt_id']='ATT-1'
        if hashless:assignment['contradiction_result_ids']=[dependency]
        base=Path(self.tmp.name);path=base/'public-assignment.yaml';output=base/'public-packet.yaml'
        qs.save_yaml(path,assignment)
        def call(command,*args):
            return subprocess.run([sys.executable,'-B',str(Path(qs.__file__)) ,command,'--root',str(base),'--run-id','RUN-1',*args],text=True,capture_output=True)
        assigned=call('assign','--assignment',str(path));self.assertEqual(assigned.returncode,0,assigned.stdout+assigned.stderr)
        result=call('context','--assignment',str(path),'--output',str(output))
        return result,output

    def test_public_planner_context_reads_exact_latest_replacement_without_selecting_graph(self):
        result,output=self.public_context()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        packet=qs.load_yaml(output)
        self.assertEqual(packet['architecture_selection_state'],'INCOMPLETE')
        self.assertEqual(packet['plan_dependency_selection_state'],'REVISION_IN_PROGRESS')
        self.assertNotIn('architecture_ref',packet)
        self.assertEqual([row['source']['result_id'] for row in packet['accepted_dependencies']],['S-000-R2'])
        self.assertEqual(packet['accepted_dependencies'][0]['source']['sha256'],self.fixture.results['S-000-R2']['content_hash'])

    def test_public_revision_dependency_exception_rejects_history_workers_and_forgery(self):
        for case in ({'dependency':'S-000'},{'worker':True},{'unrelated':True},{'hashless':True},{'forged':True}):
            with self.subTest(case=case):
                fixture=ArchitectureRevisionContextTest();fixture.setUp()
                try:
                    result,output=fixture.public_context(**case)
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertEqual(json.loads(result.stdout or result.stderr)['status'],'ERROR')
                    self.assertFalse(output.exists())
                finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
