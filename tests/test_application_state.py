"""Exercise the documented CLI: plan -> draft -> outcome -> reapply.

The old workflow appended a row but reused company_role's posting and submitted
files. These tests use the same files and CLI invocations the skills now use.
"""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'application_state.py'


class ApplicationStateCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tracker = self.root / 'job_search_tracker.csv'

    def cli(self, command, *args, payload=None, ok=True):
        argv = [sys.executable, str(TOOL), command, '--root', str(self.root), *args]
        if payload is not None:
            source = self.root / 'input.json'
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            argv += ['--input', str(source)]
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def plan(self, company='Acme', role='Engineer'):
        return self.cli('plan', '--company', company, '--role', role)

    def draft(self, plan=None):
        plan = plan or self.plan()
        row = plan['record']
        row.update(fit_rating='78', deadline='2026-12-31',
                   cv_file=f"cv/main_{plan['stem']}.tex",
                   cover_letter_file=f"cover_letters/cover_{plan['stem']}.tex")
        return self.cli('draft', payload=plan)

    def legacy(self, rows):
        fields = ['date', 'company', 'role', 'status', 'notes', 'cv_file', 'deadline', 'custom']
        with self.tracker.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_reapply_keeps_both_attempts_postings_and_submitted_materials(self):
        first = self.draft()
        old_archive = Path(first['archive'])
        old_archive.mkdir(parents=True)
        for filename in ('job_posting.md', 'cv_draft.tex', 'cover_letter.tex'):
            (old_archive / filename).write_text('January submitted material', encoding='utf-8')
        closed = self.cli('update', payload={'expected': first['record'],
                          'changes': {'status': 'rejected', 'date': '2026-01-10'},
                          'note': '2026-02-01 rejected'})
        second_plan = self.plan()
        self.assertFalse(second_plan['expected'])
        self.assertNotEqual(second_plan['stem'], first['record']['application_id'])
        second = self.draft(second_plan)
        new_archive = Path(second['archive'])
        new_archive.mkdir(parents=True)
        for filename in ('job_posting.md', 'cv_draft.tex', 'cover_letter.tex'):
            (new_archive / filename).write_text('October submitted material', encoding='utf-8')
        records = self.cli('list')['applications']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['record'], closed['record'])
        self.assertNotEqual(records[0]['archive'], records[1]['archive'])
        self.assertNotEqual(records[0]['record']['cv_file'], records[1]['record']['cv_file'])
        for filename in ('job_posting.md', 'cv_draft.tex', 'cover_letter.tex'):
            self.assertEqual((Path(records[0]['archive']) / filename).read_text(), 'January submitted material')
            self.assertEqual((Path(records[1]['archive']) / filename).read_text(), 'October submitted material')

    def test_redraft_keeps_identity_status_submission_date_and_deadline(self):
        first = self.draft()
        submitted = self.cli('update', payload={'expected': first['record'],
                             'changes': {'status': 'interview', 'date': '2026-10-01'}})
        plan = self.plan()
        self.assertEqual(plan['record']['application_id'], first['record']['application_id'])
        plan['record'].update(deadline='', fit_rating='85', status='drafted', date='2020-01-01')
        redraft = self.cli('draft', payload=plan)
        for key in ('application_id', 'status', 'date', 'deadline'):
            self.assertEqual(redraft['record'][key], submitted['record'][key])
        self.assertEqual(redraft['record']['fit_rating'], '85')
        self.assertEqual(len(self.cli('list')['applications']), 1)

    def test_notes_round_trip_commas_quotes_newlines_and_unicode(self):
        first = self.draft(self.plan('Åcme, Inc.', 'Engineer'))
        note = '2026-10-27 rejected, "no feedback"\nNext steps: 日本語'
        updated = self.cli('update', payload={'expected': first['record'], 'note': note})
        record = self.cli('list')['applications'][0]['record']
        self.assertEqual(record['notes'], note)
        self.assertEqual(record['cv_file'], first['record']['cv_file'])
        repeated = self.cli('update', payload={'expected': updated['record'], 'note': note})
        self.assertEqual(repeated['record']['notes'], note)
        # The existing rank/scrape exclusion reader must still parse the row.
        sys.path.insert(0, str(TOOL.parent))
        self.addCleanup(sys.path.remove, str(TOOL.parent))
        from rank_state import tracker_pairs, norm
        self.assertIn((norm('Åcme, Inc.'), norm('Engineer')), tracker_pairs(self.tracker))

    def test_stale_edits_and_duplicate_draft_retries_do_not_write(self):
        plan = self.plan()
        first = self.draft(plan)
        self.cli('update', payload={'expected': first['record'], 'note': 'changed'})
        before = self.tracker.read_bytes()
        self.cli('update', payload={'expected': first['record'], 'note': 'stale'}, ok=False)
        self.cli('draft', payload=plan, ok=False)
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_legacy_rows_extra_columns_and_space_statuses_survive(self):
        old = {'company': 'Acme', 'role': 'Engineer', 'status': 'no response',
               'date': '2025', 'custom': 'keep, "me"', 'notes': 'old'}
        self.legacy([old])
        original = self.cli('list')['applications'][0]
        self.assertEqual(Path(original['archive']).name, 'acme_engineer')
        self.draft()
        records = self.cli('list')['applications']
        self.assertEqual(records[0]['record']['custom'], old['custom'])
        self.assertEqual(records[0]['record']['status'], 'no response')
        self.assertEqual(records[0]['archive'], original['archive'])

    def test_write_returns_a_reusable_snapshot_after_adding_columns(self):
        self.legacy([{'company': 'Other', 'role': 'Engineer', 'status': 'applied', 'custom': 'keep'}])
        draft = self.draft()
        self.assertIn('custom', draft['record'])
        self.cli('update', payload={'expected': draft['record'], 'note': 'new note'})
        legacy = self.cli('list')['applications'][0]['record']
        update = self.cli('update', payload={'expected': legacy, 'note': 'legacy note'})
        self.cli('update', payload={'expected': update['record'], 'note': 'another note'})

    def test_ambiguous_legacy_archives_are_never_selected(self):
        self.legacy([{'company': 'Acme', 'role': 'Engineer', 'status': 'rejected', 'date': '2025'},
                     {'company': 'Acme', 'role': 'Engineer', 'status': 'interview', 'date': '2026'}])
        records = self.cli('list')['applications']
        self.assertTrue(all(r['archive'] is None and r['warning'] for r in records))
        self.cli('plan', '--company', 'Acme', '--role', 'Engineer', ok=False)
        migrated = self.cli('update', payload={'expected': records[1]['record'], 'separate_archive': True})
        self.assertIsNotNone(migrated['archive'])
        self.assertNotEqual(Path(migrated['archive']).name, 'acme_engineer')
        self.assertFalse(Path(migrated['archive']).exists())  # No guessed copying.

    def test_draft_progress_needs_submission_date_and_valid_status(self):
        first = self.draft()
        before = self.tracker.read_bytes()
        for changes in ({'status': 'applied'}, {'status': 'imaginary'},
                        {'status': 'applied', 'date': 'yesterday'}, {'application_id': '../bad'}):
            self.cli('update', payload={'expected': first['record'], 'changes': changes}, ok=False)
            self.assertEqual(self.tracker.read_bytes(), before)

    def test_plan_is_read_only_and_a_concurrent_plan_cannot_add_second_open_attempt(self):
        a, b = self.plan(), self.plan()
        self.assertFalse(self.tracker.exists())
        self.draft(a)
        before = self.tracker.read_bytes()
        self.cli('draft', payload=b, ok=False)
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_malformed_csv_and_lock_fail_without_losing_data(self):
        self.tracker.write_text('company,role,status\nAcme,Engineer,applied,EXTRA\n')
        before = self.tracker.read_bytes()
        self.cli('list', ok=False)
        self.assertEqual(self.tracker.read_bytes(), before)
        lock = self.tracker.with_suffix('.csv.lock')
        lock.write_text('writer in progress')
        self.cli('update', payload={}, ok=False)
        self.assertTrue(lock.exists())
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_short_legacy_row_gets_empty_deadline_without_shifting_document_paths(self):
        header = 'date,company,sector,role,role_type,channel,status,contact_person,fit_rating,notes,cv_file,cover_letter_file,source,deadline'
        self.tracker.write_text(header + '\n2025,Acme,tech,Engineer,fulltime,portal,rejected,,78,old,cv/old.tex,cover_letters/old.tex,https://example.com/job\n')
        self.draft()
        old = self.cli('list')['applications'][0]['record']
        self.assertEqual(old['deadline'], '')
        self.assertEqual(old['cv_file'], 'cv/old.tex')
        self.assertEqual(old['cover_letter_file'], 'cover_letters/old.tex')
        self.assertEqual(old['source'], 'https://example.com/job')

    def test_new_tool_matches_canonical_tracker_vocabulary(self):
        sys.path.insert(0, str(TOOL.parent))
        self.addCleanup(sys.path.remove, str(TOOL.parent))
        import application_state
        text = (TOOL.parent.parent / '.claude/commands/outcome.md').read_text()
        block = text.split('## Tracker status vocabulary\n', 1)[1].split('\n---', 1)[0]
        import re
        enum_line = next(line for line in block.splitlines() if line.startswith('`drafted` |'))
        declared = set(re.findall(r'`([a-z_]+)`', enum_line))
        finals = set(re.findall(r'`([a-z_]+)`', block.split('- **Final**', 1)[1].split('\n', 1)[0]))
        self.assertEqual(application_state.STATUSES, declared)
        self.assertEqual(application_state.FINAL, finals)

    def test_distinct_role_punctuation_does_not_select_another_open_attempt(self):
        first = self.draft(self.plan(role='C++ Engineer'))
        second = self.plan(role='C Engineer')
        self.assertIsNone(second['expected'])
        self.assertNotEqual(second['stem'], first['record']['application_id'])
        self.draft(second)
        reused = self.plan(company=' acme ', role=' c++ ENGINEER ')
        self.assertEqual(reused['record']['application_id'], first['record']['application_id'])

    def test_unsafe_ids_and_symlink_archives_are_rejected(self):
        for ident in ('../escape', '/tmp/escape'):
            plan = self.plan()
            plan['record']['application_id'] = ident
            self.cli('draft', payload=plan, ok=False)
            self.assertFalse(self.tracker.exists())
        if sys.platform == 'win32':
            return  # Creating directory symlinks requires Windows developer mode.
        plan = self.plan()
        archive = Path(plan['archive'])
        archive.parent.mkdir(parents=True)
        archive.symlink_to(self.root, target_is_directory=True)
        self.cli('draft', payload=plan, ok=False)
        self.assertFalse(self.tracker.exists())

    def test_nonfinite_scores_rejected_and_long_nonlatin_names_fit_filename(self):
        plan = self.plan('公司' * 100, '開発者' * 100)
        self.assertLess(len(plan['stem'].encode('utf-8')), 200)
        for score in ('NaN', 'Infinity', '-1', '101', 'good'):
            plan['record']['fit_rating'] = score
            self.cli('draft', payload=plan, ok=False)
        self.assertFalse(self.tracker.exists())

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(self):
        sys.path.insert(0, str(TOOL.parent))
        self.addCleanup(sys.path.remove, str(TOOL.parent))
        import application_state
        first = self.draft()
        before = self.tracker.read_bytes()
        with patch.object(application_state.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                application_state.save_tracker(self.tracker, list(first['record']), [first['record']])
        self.assertEqual(self.tracker.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.applications.*.tmp')))


if __name__ == '__main__':
    unittest.main()
