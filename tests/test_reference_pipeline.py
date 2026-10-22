"""Regression tests use synthetic sources in TemporaryDirectory, never project data."""
import argparse
import contextlib
import copy
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1] / '.agents/skills/textoria-entity-extractor/scripts'
sys.path.insert(0, str(SCRIPTS))
import entity_pipeline as pipeline
import convert_exports as exports


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.source = self.root / 'synthetic.txt'
        self.source.write_text('吳三桂，文中簡稱三桂。鄭經簡稱經。漳州簡稱漳，泉州簡稱泉。牛、馬。福州又稱榕城。', encoding='utf-8')
        self.quiet(pipeline.cmd_init, input=str(self.source), out=str(self.work), chunk_chars=300, overlap=40)
        self.quiet(pipeline.cmd_next, work=str(self.work), max_paragraphs=4, max_chars=1200)

    def quiet(self, function, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(argparse.Namespace(**kwargs))

    def merge(self, entries):
        response = self.work / 'response.json'
        pipeline.save(response, {'paragraphs': [{'id': 'p00001', 'entities': entries}]})
        self.quiet(pipeline.cmd_merge, work=str(self.work), response=str(response))
        return pipeline.load(self.work / 'registry.json', [])

    def entity(self, name, typ='person', **kw):
        return dict(name=name, type=typ, evidence=self.source.read_text(), **kw)

    def group(self, canonical, members=None, aliases=None, **kw):
        return dict(canonical_id=canonical, members=members or [canonical], aliases=aliases or [],
                    reason='Synthetic source explicitly establishes the identity',
                    evidence=[{'paragraph': 'p00001', 'evidence': self.source.read_text()}], **kw)

    def resolve(self, reg, groups, excluded=()):
        return pipeline.resolve_registry(reg, {'entities': groups, 'excluded': list(excluded)},
                                         pipeline.load(self.work / 'paragraphs.json', []))

    def fixture(self):
        reg = self.merge([self.entity('吳三桂', aliases=['三桂']), self.entity('三桂'),
                          self.entity('鄭經'), self.entity('經'), self.entity('漳州', 'place'),
                          self.entity('漳', 'place'), self.entity('泉州', 'place'),
                          self.entity('泉', 'place'), self.entity('牛', 'animal'),
                          self.entity('馬', 'animal'), self.entity('福州', 'place', aliases=['榕城'])])
        ids = {r['name']: r['id'] for r in reg}
        groups = [self.group(ids['吳三桂'], [ids['吳三桂'], ids['三桂']], ['三桂']),
                  self.group(ids['鄭經']), self.group(ids['漳州']), self.group(ids['泉州']),
                  self.group(ids['牛'], name_kind='full'), self.group(ids['馬'], name_kind='full'),
                  self.group(ids['福州'], aliases=['榕城'])]
        excluded = [{'id': ids[n], 'reason': 'Single-character abbreviation'} for n in '經漳泉']
        return self.resolve(reg, groups, excluded)

    def test_merge_does_not_expand_alias(self):
        reg = self.merge([self.entity('吳三桂', aliases=['三桂'])])
        self.assertEqual([r['name'] for r in reg], ['吳三桂'])

    def test_canonical_rows_and_single_complete_names(self):
        rows = pipeline.reference_rows(self.fixture())
        self.assertIn(['吳三桂', 'person', '三桂'], rows)
        self.assertNotIn('三桂', [r[0] for r in rows])
        self.assertIn(['牛', 'animal', ''], rows)
        self.assertIn(['馬', 'animal', ''], rows)
        for row in rows:
            self.assertNotIn(row[0], '經漳泉')
            self.assertFalse(set(row[2].split('|')) & set('經漳泉'))

    def test_legacy_registry_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'Canonical review required'):
            pipeline.reference_rows(self.merge([self.entity('經')]))

    def test_single_name_requires_full_decision(self):
        reg = self.merge([self.entity('牛', 'animal')])
        with self.assertRaisesRegex(ValueError, 'name_kind=full'):
            self.resolve(reg, [self.group(reg[0]['id'])])

    def test_single_alias_rejected_for_all_types(self):
        for typ in ('person', 'place', 'artifact', 'animal'):
            with self.subTest(typ=typ):
                reg = [{'id': 'a', 'name': '鄭經', 'type': typ}]
                with self.assertRaisesRegex(ValueError, 'at least two'):
                    self.resolve(reg, [self.group('a', aliases=['經'])])

    def test_incomplete_review_and_duplicate_members_rejected(self):
        reg = self.merge([self.entity('吳三桂'), self.entity('三桂')])
        with self.assertRaisesRegex(ValueError, 'every registry ID'):
            self.resolve(reg, [self.group(reg[0]['id'])])
        with self.assertRaisesRegex(ValueError, 'repeated member'):
            self.resolve(reg, [self.group(reg[0]['id']), self.group(reg[0]['id'])])

    def test_invalid_evidence_cannot_merge_or_mutate_input(self):
        reg = self.merge([self.entity('吳三桂'), self.entity('三桂')])
        before = copy.deepcopy(reg)
        group = self.group(reg[0]['id'], [r['id'] for r in reg], ['三桂'])
        group['evidence'] = [{'paragraph': 'p00001', 'evidence': 'invented'}]
        with self.assertRaisesRegex(ValueError, 'exact paragraph'):
            self.resolve(reg, [group])
        self.assertEqual(reg, before)
        group['evidence'] = [{'paragraph': 'p00001', 'evidence': '吳三桂'}]
        # The short form is contained in the full name; co-occurrence alone is not
        # identity evidence. The explicit semantic decision is still required.
        group.pop('reason')
        with self.assertRaisesRegex(ValueError, 'semantic reason'):
            self.resolve(reg, [group])

    def test_homonyms_and_ambiguous_shared_alias_remain_separate(self):
        reg = self.merge([self.entity('吳三桂', identity_key='person-A', aliases=['三桂']),
                          self.entity('吳三桂', identity_key='person-B', aliases=['三桂'])])
        self.assertEqual(len(reg), 2)
        resolved = self.resolve(reg, [self.group(r['id'], aliases=['三桂']) for r in reg])
        self.assertEqual(pipeline.reference_rows(resolved), [['吳三桂', 'person', '三桂']] * 2)
        self.assertEqual(len({r['id'] for r in resolved}), 2)

    def test_containment_does_not_merge_without_explicit_decision(self):
        reg = self.merge([self.entity('吳三桂'), self.entity('三桂')])
        rows = pipeline.reference_rows(self.resolve(reg, [self.group(r['id']) for r in reg]))
        self.assertEqual(len(rows), 2)

    def test_csv_export_and_both_platforms(self):
        resolved = self.fixture()
        pipeline.save(self.work / 'registry.json', resolved)
        self.quiet(pipeline.cmd_export, work=str(self.work), output_dir=str(self.root / 'csv'),
                   timezone='Asia/Taipei', no_alias=False, reviewed_only=False)
        source = next((self.root / 'csv').glob('*.csv'))
        original = source.read_bytes()
        self.assertTrue(original.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(original.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['name', 'type', 'alias'])
        self.assertEqual(rows.count(['吳三桂', 'person', '三桂']), 1)
        self.assertNotIn('三桂', [r[0] for r in rows])
        paths = exports.convert(source, ['markus', 'docusky'], full_single_names=['牛', '馬'])
        txt = Path(paths[0]).read_text()
        terms = [r.split('\t')[1] for r in txt.splitlines()[1:]]
        self.assertIn('三桂', terms)  # Confirmed multi-character platform matching term.
        self.assertIn('牛', terms)
        self.assertFalse(set(terms) & set('經漳泉'))
        with ZipFile(paths[1]) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        terms = [node.text or '' for node in sheet.iter(f'{{{exports.SHEET_NS}}}t')]
        self.assertIn('馬', terms)
        self.assertFalse(set(terms) & set('經漳泉'))
        self.assertEqual(source.read_bytes(), original)

    def test_two_column_export_does_not_expand_alias(self):
        pipeline.save(self.work / 'registry.json', self.fixture())
        self.quiet(pipeline.cmd_export, work=str(self.work), output_dir=str(self.root / 'csv'),
                   timezone='Asia/Taipei', no_alias=True, reviewed_only=False)
        source = next((self.root / 'csv').glob('*.csv'))
        rows = list(csv.reader(io.StringIO(source.read_text(encoding='utf-8-sig'))))
        self.assertEqual(rows[0], ['name', 'type'])
        self.assertNotIn(['三桂', 'person'], rows)

    def test_legacy_single_terms_fail_before_platform_writes(self):
        for name, alias in [('經', ''), ('漳', ''), ('泉', ''), ('鄭經', '經'), ('福州', '榕城|漳')]:
            with self.subTest(name=name, alias=alias):
                source = self.root / 'legacy.csv'
                source.write_text(f'name,type,alias\n{name},place,{alias}\n', encoding='utf-8-sig')
                original = source.read_bytes()
                with self.assertRaisesRegex(ValueError, 'canonical review'):
                    exports.convert(source, ['markus', 'docusky'])
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(self.root.glob('markus-*')))
                self.assertFalse(list(self.root.glob('docusky-*')))

    def test_resolve_command_and_review_invalidation(self):
        reg = self.merge([self.entity('吳三桂', aliases=['三桂'])])
        response = self.work / 'resolution.json'
        pipeline.save(response, {'entities': [self.group(reg[0]['id'], aliases=['三桂'])]})
        self.quiet(pipeline.cmd_resolve, work=str(self.work), response=str(response))
        ready = pipeline.load(self.work / 'registry.json', [])
        self.assertEqual(pipeline.reference_rows(ready), [['吳三桂', 'person', '三桂']])
        paragraphs = pipeline.load(self.work / 'paragraphs.json', [])
        paragraphs.append(dict(paragraphs[0], id='p00002', status='pending'))
        pipeline.save(self.work / 'paragraphs.json', paragraphs)
        pipeline.save(self.work / 'batch.json', {'paragraphs': [paragraphs[1]]})
        pipeline.save(response, {'paragraphs': [{'id': 'p00002', 'entities': []}]})
        self.quiet(pipeline.cmd_merge, work=str(self.work), response=str(response))
        with self.assertRaisesRegex(ValueError, 'Canonical review required'):
            pipeline.reference_rows(pipeline.load(self.work / 'registry.json', []))

    def test_default_context_keys_do_not_merge_homonyms(self):
        reg = self.merge([self.entity('吳三桂')])
        paragraphs = pipeline.load(self.work / 'paragraphs.json', [])
        paragraphs.append(dict(paragraphs[0], id='p00002', status='pending'))
        pipeline.save(self.work / 'paragraphs.json', paragraphs)
        pipeline.save(self.work / 'batch.json', {'paragraphs': [paragraphs[1]]})
        response = self.work / 'response2.json'
        pipeline.save(response, {'paragraphs': [{'id': 'p00002', 'entities': [self.entity('吳三桂')]}]})
        self.quiet(pipeline.cmd_merge, work=str(self.work), response=str(response))
        reg = pipeline.load(self.work / 'registry.json', [])
        self.assertEqual(len(reg), 2)
        self.assertNotEqual(reg[0]['id'], reg[1]['id'])

    def test_unknown_single_abbreviation_is_excluded_without_inventing_name(self):
        reg = self.merge([self.entity('經')])
        resolved = self.resolve(reg, [], [{'id': reg[0]['id'], 'reason': 'Unknown full name'}])
        self.assertEqual(pipeline.reference_rows(resolved), [])

    def test_platform_single_complete_names_need_explicit_confirmation(self):
        source = self.root / 'animals.csv'
        source.write_text('name,type,alias\n牛,animal,\n馬,animal,\n', encoding='utf-8-sig')
        with self.assertRaisesRegex(ValueError, 'canonical review'):
            exports.read_names(source)
        self.assertEqual(exports.read_names(source, ['牛', '馬']), [('牛', 'animal'), ('馬', 'animal')])

    def test_review_does_not_inherit_legacy_aliases(self):
        reg = self.merge([self.entity('鄭經', aliases=['經'])])
        resolved = self.resolve(reg, [self.group(reg[0]['id'])])
        self.assertEqual(pipeline.reference_rows(resolved), [['鄭經', 'person', '']])

    def test_shared_alias_never_merges_different_canonical_names(self):
        text = '甲地調查局簡稱調查局；乙地調查局也簡稱調查局，兩者為不同機構。'
        reg = [{'id': 'A', 'name': '甲地調查局', 'type': 'organization'},
               {'id': 'B', 'name': '乙地調查局', 'type': 'organization'}]
        groups = [dict(canonical_id=r['id'], members=[r['id']], aliases=['調查局'],
                       reason='Distinct organizations in the supplied context',
                       evidence=[{'paragraph': 'p1', 'evidence': text}]) for r in reg]
        resolved = pipeline.resolve_registry(reg, {'entities': groups}, [{'id': 'p1', 'text': text}])
        rows = pipeline.reference_rows(resolved)
        self.assertEqual({r[0] for r in rows}, {'甲地調查局', '乙地調查局'})
        self.assertEqual(len(rows), 2)
        self.assertNotIn('調查局', [r[0] for r in rows])

    def test_canonical_name_itself_requires_source_evidence(self):
        reg = self.merge([self.entity('吳三桂')])
        group = self.group(reg[0]['id'])
        group['evidence'] = []
        with self.assertRaisesRegex(ValueError, 'Canonical name needs source evidence'):
            self.resolve(reg, [group])

    def test_platform_trimming_does_not_hide_invalid_tsv_characters(self):
        source = self.root / 'invalid.csv'
        with source.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['name', 'type', 'alias'])
            writer.writerow(['吳三桂', 'person', '三桂\t'])
        with self.assertRaisesRegex(ValueError, 'tab or line break'):
            exports.read_names(source)

    def test_no_unconditional_rongcheng_mapping(self):
        source = self.root / 'names.csv'
        source.write_text('name,type,alias\n榕城,place,\n', encoding='utf-8-sig')
        self.assertEqual(exports.read_names(source), [('榕城', 'place')])


if __name__ == '__main__':
    unittest.main()
