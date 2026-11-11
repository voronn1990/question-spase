"""Revision history remains immutable and outside current model coverage."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from test_architecture_integration import CompleteArchitectureModel, bind_architecture
from test_iceberg_contracts import _refresh_hash_bindings
from test_model_contracts import CompleteModel, model_validation as model, load_yaml, write, digest
from architecture_validation import architecture_graph_sha256
from prep_validation import canonical_yaml_bytes


def retain_revision_history(f):
    """Build map v1->v2, Synthesis v1->v2, PLAN v1->v2 and audit v2."""
    original = copy.deepcopy(f.docs)
    old_map = copy.deepcopy(original['VIEW-CURRENT-RESULT'])
    old_map.update(id='VIEW-CURRENT-OLD', assignment_id='ASG-VIEW-CURRENT-OLD')
    old_map['iceberg_view']['view_id'] = 'ICE-CURRENT-OLD'
    f.add_result('VIEW-CURRENT-OLD', 'OPERATOR', old_map['author_role'], old_map['agent_id'],
                 'operators/VIEW-CURRENT-OLD.yaml', old_map)
    old_binding = {
        'view_id': 'ICE-CURRENT-OLD', 'view_kind': 'CURRENT_REALITY',
        'result_id': 'VIEW-CURRENT-OLD',
        'result_sha256': f.results['VIEW-CURRENT-OLD']['content_hash'], 'revision': '1',
    }
    old_bindings = [old_binding, copy.deepcopy(f.projection['DESIRED_REALITY'])]
    old_synthesis = copy.deepcopy(original['SYNTHESIS-001'])
    old_synthesis.update(id='SYNTHESIS-OLD', assignment_id='ASG-SYNTHESIS-OLD',
                         iceberg_view_bindings=old_bindings)
    for trace in [old_synthesis['iceberg_mechanism_trace']['chosen']] + old_synthesis['iceberg_mechanism_trace']['alternatives']:
        trace['member_refs'] = [item.replace('ICE-CURRENT-001#', 'ICE-CURRENT-OLD#') for item in trace['member_refs']]
    f.add_result('SYNTHESIS-OLD', 'SYNTHESIS', old_synthesis['author_role'], old_synthesis['agent_id'],
                 'synthesis/SYNTHESIS-OLD.yaml', old_synthesis)
    old_reference = {
        'synthesis_id': 'SYNTHESIS-OLD',
        'synthesis_sha256': f.results['SYNTHESIS-OLD']['content_hash'],
        'graph_sha256': architecture_graph_sha256(old_synthesis['architecture_graph']),
    }
    for rid in ('S-000', 'S-001'):
        old = copy.deepcopy(original[rid])
        old.update(id=rid+'-OLD', assignment_id='ASG-'+rid+'-OLD', architecture_ref=old_reference)
        old['iceberg_trace']['view_bindings'] = copy.deepcopy(old_bindings)
        f.add_result(rid+'-OLD', 'PLAN', old['author_role'], old['agent_id'],
                     'plan/'+rid+'-OLD.yaml', old)
        f.docs[rid].update(supersedes=rid+'-OLD', revision='2', plan_version='2')
        f.docs[rid]['identity_result']['plan_version'] = '2'
    old_audit = copy.deepcopy(original['AUDIT-001'])
    old_audit.update(id='AUDIT-OLD', assignment_id='ASG-AUDIT-OLD',
                     iceberg_view_bindings=old_bindings, architecture_ref=old_reference)
    for detail in old_audit['area_details'].values():
        detail['hashes'] = [f.results['S-000-OLD']['content_hash']]
    f.add_result('AUDIT-OLD', 'FINAL_AUDIT', old_audit['author_role'], old_audit['agent_id'],
                 'audit/AUDIT-OLD.yaml', old_audit)
    current = f.docs['VIEW-CURRENT-RESULT']
    current['supersedes'] = 'VIEW-CURRENT-OLD'
    current['iceberg_view'].update(revision='2', supersedes_view_id='ICE-CURRENT-OLD')
    current['iceberg_view']['source_bindings'].append({
        'source_kind': 'ACCEPTED_RESULT', 'source_id': 'VIEW-CURRENT-OLD',
        'source_sha256': f.results['VIEW-CURRENT-OLD']['content_hash'],
        'source_path': f.results['VIEW-CURRENT-OLD']['path'],
    })
    f.sync('VIEW-CURRENT-RESULT')
    f.projection['CURRENT_REALITY'].update(revision='2',
        result_sha256=f.results['VIEW-CURRENT-RESULT']['content_hash'])
    f.docs['SYNTHESIS-001'].update(supersedes='SYNTHESIS-OLD', revision='2')
    f.docs['AUDIT-001'].update(supersedes='AUDIT-OLD', revision='2')
    _refresh_hash_bindings(f)
    bind_architecture(f)
    resolved = model.resolve_architecture_model(
        f.run, f.bank, f.results, f.docs, {'S-000':'S-000', 'S-001':'S-001'},
        load_yaml=load_yaml, sha=digest,
    )
    f.docs['AUDIT-001']['architecture_review']['structural_report_sha256'] = digest(canonical_yaml_bytes(resolved['report']))
    f.sync('AUDIT-001')
    _refresh_hash_bindings(f)
    # An explicit selected audit has its own GATE-6, retaining the old canonical
    # gate path rather than silently choosing it among multiple audits.
    gate = load_yaml(f.run/'gates/GATE-6.yaml')
    write(f.run/'gates/GATE-6/AUDIT-001.yaml', gate)
    return {'S-000':'S-000', 'S-001':'S-001'}


def history_errors(f, projection):
    return model.validate_model(f.run, f.bank, f.results, f.docs, 'final',
                                load_yaml, digest, projection,
                                current_final_audit_id='AUDIT-001')


class RevisionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = CompleteArchitectureModel(Path(self.tmp.name))
        self.projection = retain_revision_history(self.fixture)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retained_maps_synthesis_plans_and_audit_pass_without_deletion(self):
        f = self.fixture
        before = {rid: (f.run/entry['path']).read_bytes() for rid, entry in f.results.items() if rid.endswith('-OLD')}
        self.assertEqual(history_errors(f, self.projection), [])
        self.assertEqual(before, {rid: (f.run/f.results[rid]['path']).read_bytes() for rid in before})

    def test_old_synthesis_is_not_a_gate4_required_current_object(self):
        f = self.fixture
        errors = history_errors(f, self.projection)
        self.assertFalse(any('GATE-4:missing_required_object:SYNTHESIS-OLD' in item for item in errors), errors)

    def test_reused_relation_ids_in_history_form_one_active_relation(self):
        f = self.fixture
        scope = model.resolve_model_revision_scope(f.results, f.docs, self.projection, 'AUDIT-001')
        self.assertEqual(scope['errors'], [])
        self.assertEqual(scope['active_synthesis_ids'], {'SYNTHESIS-001'})
        self.assertIn('SYNTHESIS-OLD', scope['historical_result_ids'])
        self.assertFalse(any('iceberg_edge:' in item and item.endswith(':id') for item in history_errors(f, self.projection)))

    def test_historical_bytes_are_still_checked(self):
        f = self.fixture
        path = f.run/f.results['SYNTHESIS-OLD']['path']
        path.write_bytes(path.read_bytes()+b'\n')
        self.assertIn('model_scope:SYNTHESIS-OLD:immutable_result_hash', history_errors(f, self.projection))

    def test_broken_historical_view_hash_is_rejected_after_rehashing_consumer(self):
        f = self.fixture
        f.docs['SYNTHESIS-OLD']['iceberg_view_bindings'][0]['result_sha256'] = '0'*64
        f.sync('SYNTHESIS-OLD')
        self.assertTrue(any('iceberg_consumer:SYNTHESIS-OLD:view_binding:CURRENT_REALITY' in item for item in history_errors(f, self.projection)))

    def test_broken_historical_semantic_relation_is_not_hidden(self):
        f = self.fixture
        f.docs['SYNTHESIS-OLD']['semantic_edges'][0]['from_id'] = 'H-MISSING'
        f.sync('SYNTHESIS-OLD')
        self.assertTrue(any('historical:SYNTHESIS-OLD:iceberg_edge:' in item and 'from_binding' in item for item in history_errors(f, self.projection)))

    def test_historical_trace_cannot_borrow_an_active_edge_or_execution_endpoint(self):
        f = self.fixture
        current_edge = f.edge("EDGE-CURRENT-ONLY", "ENABLES", "R", "R-001", "D", "D-001")
        f.docs["D-001"]["semantic_edges"] = [current_edge]
        f.sync("D-001")
        historical = f.docs["SYNTHESIS-OLD"]
        historical["iceberg_mechanism_trace"]["chosen"]["edge_ids"].append("EDGE-CURRENT-ONLY")
        historical["semantic_edges"].append(
            f.edge("EDGE-FOREIGN-TASK", "ENABLES", "D", "D-001", "T", "T-CURRENT-ONLY"))
        f.docs["S-001"]["tasks"].append({"id": "T-CURRENT-ONLY"})
        f.sync("S-001")
        f.sync("SYNTHESIS-OLD")
        errors = history_errors(f, self.projection)
        self.assertIn("iceberg_synthesis:SYNTHESIS-OLD:trace:0:edge_ids", errors)
        self.assertIn("historical:SYNTHESIS-OLD:iceberg_edge:EDGE-FOREIGN-TASK:to_binding", errors)

    def test_historical_consumer_cannot_claim_future_map_member(self):
        f = self.fixture
        f.docs['SYNTHESIS-OLD']['iceberg_mechanism_trace']['chosen']['member_refs'] = ['ICE-CURRENT-001#H-001']
        f.sync('SYNTHESIS-OLD')
        self.assertTrue(any('iceberg_synthesis:SYNTHESIS-OLD:trace:0:member_refs' in item for item in history_errors(f, self.projection)))

    def test_forked_synthesis_history_is_rejected(self):
        f = self.fixture
        branch = copy.deepcopy(f.docs['SYNTHESIS-001'])
        branch.update(id='SYNTHESIS-BRANCH', assignment_id='ASG-SYNTHESIS-BRANCH')
        f.add_result('SYNTHESIS-BRANCH', 'SYNTHESIS', branch['author_role'], branch['agent_id'],
                     'synthesis/SYNTHESIS-BRANCH.yaml', branch)
        self.assertIn('model_scope:synthesis:SYNTHESIS-OLD:branch', history_errors(f, self.projection))

    def test_old_synthesis_cannot_be_promoted_by_exact_current_plan_reference(self):
        f = self.fixture
        old_ref = copy.deepcopy(f.docs['S-000-OLD']['architecture_ref'])
        for rid in self.projection:
            f.docs[rid]['architecture_ref'] = copy.deepcopy(old_ref)
            f.sync(rid)
        self.assertIn('model_scope:synthesis:historical_promotion', history_errors(f, self.projection))

    def test_current_synthesis_marked_stale_does_not_fall_back_to_history(self):
        f = self.fixture
        write(f.run/'state/current.yaml', {'stale_result_ids':['SYNTHESIS-001']})
        errors = history_errors(f, self.projection)
        self.assertIn('model_scope:synthesis:selected_stale', errors)

    def test_full_model_rejects_malformed_selected_synthesis_id_without_crashing(self):
        f = self.fixture
        for value in ([], {}, True, None, 17):
            with self.subTest(value=value):
                f.docs['S-000']['architecture_ref']['synthesis_id'] = value
                errors = history_errors(f, self.projection)
                self.assertIn('model_scope:synthesis:exact_reference', errors)

    def test_full_model_rejects_malformed_plan_identity_without_crashing(self):
        f = self.fixture
        for value in ([], True, None, 17):
            with self.subTest(value=value):
                f.docs['S-000']['identity_result'] = value
                # Exercise inference as well as an explicit logical map.
                errors = model.validate_model(f.run, f.bank, f.results, f.docs, 'final',
                                              load_yaml, digest,
                                              current_final_audit_id='AUDIT-001')
                self.assertTrue(any('identity' in item for item in errors), errors)

    def test_in_memory_history_cannot_diverge_from_exact_accepted_bytes(self):
        f = self.fixture
        f.docs['SYNTHESIS-OLD']['chosen_mechanism'] = 'unrecorded replacement conclusion'
        self.assertIn('model_scope:SYNTHESIS-OLD:document_bytes_binding', history_errors(f, self.projection))

    def test_historical_plan_and_audit_reviews_do_not_become_current_execution_nodes(self):
        f = self.fixture
        f.add_review('REVIEW-OLD-PLAN', 5, 'S-000-OLD', 'SUBJECT', 'OLD-PLAN')
        f.add_review('REVIEW-OLD-AUDIT', 6, 'AUDIT-OLD', 'SUBJECT', 'OLD-AUDIT')
        self.assertEqual(history_errors(f, self.projection), [])
        scope = model.resolve_model_revision_scope(f.results, f.docs, self.projection, 'AUDIT-001')
        self.assertTrue({'REVIEW-OLD-PLAN', 'REVIEW-OLD-AUDIT'}.issubset(scope['historical_result_ids']))

    def reuse_old_plan_review(self):
        f = self.fixture
        f.add_review('REVIEW-REUSED-PLAN', 5, 'S-000-OLD', 'SUBJECT', 'REUSED-PLAN')
        f.docs['SYNTHESIS-001']['execution_edges'].extend([
            {'from_type':'D', 'from_id':'D-001', 'to_type':'REVIEW', 'to_id':'REVIEW-REUSED-PLAN'},
            {'from_type':'REVIEW', 'from_id':'REVIEW-REUSED-PLAN', 'to_type':'T', 'to_id':'T-000'},
        ])
        f.sync('SYNTHESIS-001')
        bind_architecture(f)
        resolved = model.resolve_architecture_model(
            f.run, f.bank, f.results, f.docs, self.projection,
            load_yaml=load_yaml, sha=digest,
        )
        f.docs['AUDIT-001']['architecture_review']['structural_report_sha256'] = digest(canonical_yaml_bytes(resolved['report']))
        f.sync('AUDIT-001')
        _refresh_hash_bindings(f)
        write(f.run/'gates/GATE-6/AUDIT-001.yaml', load_yaml(f.run/'gates/GATE-6.yaml'))
        return f

    def test_current_execution_reuses_exact_old_target_review(self):
        f = self.reuse_old_plan_review()
        scope = model.resolve_model_revision_scope(f.results, f.docs, self.projection, 'AUDIT-001')
        self.assertIn('REVIEW-REUSED-PLAN', scope['active_result_ids'])
        self.assertIn('S-000-OLD', scope['historical_result_ids'])
        self.assertEqual(history_errors(f, self.projection), [])

    def test_reused_old_target_review_keeps_target_hash_validation(self):
        f = self.reuse_old_plan_review()
        f.docs['REVIEW-REUSED-PLAN']['target_hash'] = '0'*64
        f.sync('REVIEW-REUSED-PLAN')
        self.assertIn('review:REVIEW-REUSED-PLAN:target_binding', history_errors(f, self.projection))

    def test_stale_review_cannot_be_promoted_by_current_execution(self):
        f = self.reuse_old_plan_review()
        write(f.run/'state/current.yaml', {'stale_result_ids':['REVIEW-REUSED-PLAN']})
        self.assertIn('model_scope:review:REVIEW-REUSED-PLAN:current_stale', history_errors(f, self.projection))

    def test_historical_execution_reference_does_not_promote_retained_review(self):
        f = self.fixture
        f.add_review('REVIEW-OLD-PLAN', 5, 'S-000-OLD', 'SUBJECT', 'OLD-PLAN')
        f.docs['SYNTHESIS-OLD']['execution_edges'].extend([
            {'from_type':'D', 'from_id':'D-001', 'to_type':'REVIEW', 'to_id':'REVIEW-OLD-PLAN'},
            {'from_type':'REVIEW', 'from_id':'REVIEW-OLD-PLAN', 'to_type':'T', 'to_id':'T-000'},
        ])
        f.sync('SYNTHESIS-OLD')
        scope = model.resolve_model_revision_scope(f.results, f.docs, self.projection, 'AUDIT-001')
        self.assertIn('REVIEW-OLD-PLAN', scope['historical_result_ids'])

    def test_current_review_use_closes_over_exact_review_target(self):
        f = self.reuse_old_plan_review()
        f.add_review('REVIEW-OUTER', 6, 'REVIEW-REUSED-PLAN', 'SUBJECT', 'OUTER')
        synthesis = f.docs['SYNTHESIS-001']
        synthesis['execution_edges'] = [
            {**edge, **{key:'REVIEW-OUTER' for key in ('from_id','to_id') if edge.get(key)=='REVIEW-REUSED-PLAN'}}
            for edge in synthesis['execution_edges']
        ]
        f.sync('SYNTHESIS-001')
        bind_architecture(f)
        scope = model.resolve_model_revision_scope(f.results, f.docs, self.projection, 'AUDIT-001')
        self.assertTrue({'REVIEW-OUTER','REVIEW-REUSED-PLAN'}.issubset(scope['active_result_ids']))
        self.assertIn('S-000-OLD', scope['historical_result_ids'])

    def test_broken_historical_execution_reference_is_not_hidden(self):
        f = self.fixture
        f.docs['SYNTHESIS-OLD']['execution_edges'][0]['to_id'] = 'Q-MISSING'
        f.sync('SYNTHESIS-OLD')
        self.assertTrue(any('historical:SYNTHESIS-OLD:execution_edge:0:to_binding' in item for item in history_errors(f, self.projection)))

    def test_historical_execution_cycle_is_not_hidden(self):
        f = self.fixture
        edges = f.docs['SYNTHESIS-OLD']['execution_edges']
        first = edges[0]
        edges.append({'from_type':first['to_type'], 'from_id':first['to_id'],
                      'to_type':first['from_type'], 'to_id':first['from_id']})
        f.sync('SYNTHESIS-OLD')
        self.assertIn('historical:SYNTHESIS-OLD:execution_graph_cycle', history_errors(f, self.projection))

    def test_historical_plan_keeps_exact_own_synthesis_binding(self):
        f = self.fixture
        f.docs['S-000-OLD']['architecture_ref']['synthesis_sha256'] = '0'*64
        f.sync('S-000-OLD')
        self.assertTrue(any('historical:S-000-OLD:architecture_ref:synthesis_hash' in item for item in history_errors(f, self.projection)))

    def test_historical_plan_cannot_mix_own_synthesis_with_new_map(self):
        f = self.fixture
        f.docs['S-000-OLD']['iceberg_trace']['view_bindings'] = copy.deepcopy(f.docs['S-000']['iceberg_trace']['view_bindings'])
        f.sync('S-000-OLD')
        self.assertIn('historical:S-000-OLD:synthesis_view_binding', history_errors(f, self.projection))

    def test_disconnected_and_cyclic_synthesis_revisions_fail_closed(self):
        f = self.fixture
        current = f.docs['SYNTHESIS-001']
        current.pop('supersedes')
        f.sync('SYNTHESIS-001')
        errors = history_errors(f, self.projection)
        self.assertIn('model_scope:synthesis:root_cardinality:2', errors)
        self.assertIn('model_scope:synthesis:SYNTHESIS-001:initial_revision', errors)
        current['supersedes'] = 'SYNTHESIS-OLD'
        f.docs['SYNTHESIS-OLD']['supersedes'] = 'SYNTHESIS-001'
        f.sync('SYNTHESIS-OLD'); f.sync('SYNTHESIS-001')
        self.assertIn('model_scope:synthesis:cycle', history_errors(f, self.projection))

    def test_before_s000_revision_requires_explicit_current_research_assignment(self):
        f = self.fixture
        results = {rid:entry for rid,entry in f.results.items() if entry['artifact_type'] != 'PLAN'}
        docs = {rid:doc for rid,doc in f.docs.items() if rid in results}
        missing = model.resolve_model_revision_scope(results, docs, current_final_audit_id='AUDIT-001')
        self.assertIn('model_scope:synthesis:explicit_current_assignment_required', missing['errors'])
        selected = model.resolve_model_revision_scope(results, docs, current_final_audit_id='AUDIT-001',
                                                       current_assignment_results={'SYNTHESIS-001'})
        self.assertEqual(selected['errors'], [])
        self.assertEqual(selected['active_synthesis_ids'], {'SYNTHESIS-001'})

    def test_pending_retry_prevents_accepting_old_assigned_synthesis_as_current(self):
        f = self.fixture
        entry = f.results['SYNTHESIS-001']
        write(f.run/'state/assignments.yaml', {
            'current_attempts':{entry['assignment_id']:'ATT-2'},
            'registered':{entry['assignment_id']+'\0'+entry['attempt_id']:{
                'status':'ACCEPTED', 'assignment':{**entry, 'output_path':entry['path']},
            }},
        })
        errors = history_errors(f, self.projection)
        self.assertIn('model_scope:synthesis:current_assignment_binding', errors)


class TransitionalAuditHistoryTests(unittest.TestCase):
    """A preceding release audit cannot block preparation or approve its successor."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = CompleteArchitectureModel(Path(self.tmp.name))
        self.projection = retain_revision_history(self.fixture)
        # The next audit has not been authored yet. Its predecessor is still
        # the persisted exact selector while maps/Synthesis/PLAN have advanced.
        f = self.fixture
        for rid in ('AUDIT-001', 'REVIEW-GATE-6'):
            entry = f.results.pop(rid)
            f.docs.pop(rid)
            (f.run/entry['path']).unlink()

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, phase='core'):
        f = self.fixture
        return model.validate_model(f.run, f.bank, f.results, f.docs, phase,
                                    load_yaml, digest, self.projection,
                                    current_final_audit_id='AUDIT-OLD')

    def scope(self, phase='core'):
        f = self.fixture
        return model.model_revision_scope_for_run(
            f.run, f.results, f.docs, self.projection, 'AUDIT-OLD',
            load_yaml, digest, phase=phase,
        )

    def test_exact_old_selected_audit_is_retained_in_core_but_current_in_final(self):
        f = self.fixture
        before = (f.run/f.results['AUDIT-OLD']['path']).read_bytes()
        self.assertEqual(self.errors(), [])
        self.assertIn('AUDIT-OLD', self.scope()['historical_result_ids'])
        self.assertIn('AUDIT-OLD', self.scope('final')['active_result_ids'])
        final = self.errors('final')
        self.assertIn('iceberg_audit:AUDIT-OLD:view_bindings', final)
        self.assertIn('architecture:audit:reference_mismatch', final)
        self.assertEqual((f.run/f.results['AUDIT-OLD']['path']).read_bytes(), before)

    def test_retained_selected_audit_keeps_disk_hash_guard(self):
        self.assertEqual(self.errors(), [])
        f = self.fixture
        path = f.run/f.results['AUDIT-OLD']['path']
        path.write_bytes(path.read_bytes()+b'\n')
        self.assertIn('model_scope:AUDIT-OLD:immutable_result_hash', self.errors())

    def test_retained_selected_audit_keeps_in_memory_document_guard(self):
        self.assertEqual(self.errors(), [])
        self.fixture.docs['AUDIT-OLD']['verdict'] = 'unrecorded replacement conclusion'
        self.assertIn('model_scope:AUDIT-OLD:document_bytes_binding', self.errors())

    def test_corrupt_old_audit_context_is_not_downgraded(self):
        f = self.fixture
        original = copy.deepcopy(f.docs['AUDIT-OLD'])
        for reference in (
            {**original['architecture_ref'], 'synthesis_sha256':'0'*64},
            {**original['architecture_ref'], 'graph_sha256':'0'*64},
            {**original['architecture_ref'], 'synthesis_id':'UNKNOWN-SYNTHESIS'},
            {**original['architecture_ref'], 'synthesis_id':[]},
            None,
        ):
            with self.subTest(reference=reference):
                f.docs['AUDIT-OLD'] = {**copy.deepcopy(original), 'architecture_ref':reference}
                f.sync('AUDIT-OLD')
                self.assertIn('AUDIT-OLD', self.scope()['active_result_ids'])
                self.assertTrue(self.errors())

    def test_mixed_old_synthesis_and_current_map_bindings_are_rejected(self):
        f = self.fixture
        f.docs['AUDIT-OLD']['iceberg_view_bindings'] = copy.deepcopy(f.docs['SYNTHESIS-001']['iceberg_view_bindings'])
        f.sync('AUDIT-OLD')
        self.assertIn('AUDIT-OLD', self.scope()['active_result_ids'])
        self.assertIn('model_scope:audit:AUDIT-OLD:historical_context_binding', self.errors())

    def test_corrupt_old_map_hash_cannot_become_retained_context(self):
        f = self.fixture
        f.docs['AUDIT-OLD']['iceberg_view_bindings'][0]['result_sha256'] = '0'*64
        f.sync('AUDIT-OLD')
        self.assertIn('AUDIT-OLD', self.scope()['active_result_ids'])
        self.assertTrue(any('iceberg_consumer:AUDIT-OLD:view_binding:' in item for item in self.errors()))

    def test_iceberg_only_legacy_audit_can_retain_exact_map_ancestors(self):
        f = self.fixture
        docs = copy.deepcopy(f.docs)
        for rid, doc in docs.items():
            doc.pop('architecture_ref', None)
            if f.results[rid]['artifact_type'] == 'SYNTHESIS':
                doc.pop('architecture_graph', None)
        core = model.resolve_model_revision_scope(f.results, docs, self.projection, 'AUDIT-OLD',
                                                   current_assignment_results={'SYNTHESIS-001'}, phase='core')
        final = model.resolve_model_revision_scope(f.results, docs, self.projection, 'AUDIT-OLD',
                                                    current_assignment_results={'SYNTHESIS-001'})
        self.assertEqual(core['errors'], [])
        self.assertIn('AUDIT-OLD', core['historical_result_ids'])
        self.assertIn('AUDIT-OLD', final['active_result_ids'])

    def test_standalone_iceberg_uses_the_same_phase_boundary(self):
        f = self.fixture
        # Without an explicit PLAN projection, the standalone API is supplied
        # only the current PLAN rows; retained maps/Synthesis/audit still exist.
        results = {rid:entry for rid,entry in f.results.items()
                   if entry['artifact_type'] != 'PLAN' or rid in self.projection}
        docs = {rid:doc for rid,doc in f.docs.items() if rid in results}
        _views, history, errors = model.validate_double_iceberg(
            f.run, f.bank, results, docs, 'core', load_yaml, digest,
        )
        self.assertEqual(errors, [])
        self.assertIn('AUDIT-OLD', history)
        _views, history, errors = model.validate_double_iceberg(
            f.run, f.bank, results, docs, 'final', load_yaml, digest,
        )
        self.assertNotIn('AUDIT-OLD', history)
        self.assertIn('iceberg_audit:AUDIT-OLD:view_bindings', errors)

    def test_current_context_audit_retains_pre_audit_pure_api_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            f = CompleteArchitectureModel(Path(directory))
            f.docs['AUDIT-001'].pop('area_details')
            (f.run/'gates/GATE-6.yaml').unlink()
            self.assertEqual(f.validate('core'), [])
            scope = model.model_revision_scope_for_run(
                f.run, f.results, f.docs, None, 'AUDIT-001', load_yaml, digest, phase='core',
            )
            self.assertIn('AUDIT-001', scope['active_result_ids'])


class ProjectedCurrentHistoryTests(unittest.TestCase):
    """Prospective current docs cannot bypass retained PLAN byte integrity."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = CompleteModel(Path(self.tmp.name))
        f = self.fixture
        self.results = copy.deepcopy(f.results)
        self.docs = copy.deepcopy(f.docs)
        self.projection = {}
        for old_id, new_id, predecessors, next_stages in (
            ('S-000', 'S-000-R2', [], ['S-001-R2']),
            ('S-001', 'S-001-R2', ['S-000-R2'], []),
        ):
            doc = copy.deepcopy(self.docs[old_id])
            doc.update(id=new_id, assignment_id='ASG-'+old_id, attempt_id='ATT-2',
                       revision='2', related_ids=[old_id], supersedes=old_id, plan_version='2')
            doc['identity_result']['plan_version'] = '2'
            doc['local_execution_graph'].update(predecessors=predecessors, next_stages=next_stages)
            self.docs[new_id] = doc
            # The pure projected current object has not been persisted. Its
            # old path deliberately remains the retained predecessor path.
            self.results[new_id] = {**self.results[old_id], 'result_id':new_id,
                                   'assignment_id':'ASG-'+old_id, 'attempt_id':'ATT-2',
                                   'content_hash':'b'*64}
            self.projection[new_id] = old_id
        gate = load_yaml(f.run/'gates/GATE-5.yaml')
        gate['required_objects'].extend(sorted(self.projection))
        gate['content_review']['target_hashes'].append('b'*64)
        write(f.run/'gates/GATE-5.yaml', gate)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        f = self.fixture
        return model.validate_model(f.run, f.bank, self.results, self.docs, 'final',
                                    load_yaml, digest, self.projection)

    def test_projected_current_keeps_historical_plan_disk_hash_check(self):
        self.assertEqual(self.errors(), [])
        path = self.fixture.run/self.results['S-000']['path']
        path.write_bytes(path.read_bytes()+b'\n')
        self.assertIn('model_scope:S-000:immutable_result_hash', self.errors())

    def test_projected_current_keeps_historical_plan_document_bytes_check(self):
        self.assertEqual(self.errors(), [])
        self.docs['S-000']['identity_result']['purpose'] = 'unrecorded historical conclusion'
        self.assertIn('model_scope:S-000:document_bytes_binding', self.errors())


if __name__ == '__main__':
    unittest.main()
