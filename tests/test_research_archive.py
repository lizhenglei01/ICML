from pathlib import Path

import pytest

from research_archive import Archive, compare_results, digest, read, verify_inventory


def test_private_evidence_hash_and_public_boundary(tmp_path):
    evidence = tmp_path / 'evidence.xlsx'
    evidence.write_bytes(b'private synthetic workbook')
    archive = Archive('test_archive', 'gpt-6.2_trial', tmp_path / 'public', tmp_path / 'private')
    archive.add(evidence, Path('inputs') / evidence.name, 'input')
    archive.seal('Fixture', 'stateful', {})
    manifest = read(archive.public / 'manifest.json')
    receipt_hash = manifest['private_archive']['inventory_sha256']
    assert list(archive.public.iterdir()) == [archive.public / 'manifest.json']
    assert b'private synthetic workbook' not in (archive.public / 'manifest.json').read_bytes()
    verify_inventory(archive.private, receipt_hash)
    assert digest(archive.private / 'inputs/evidence.xlsx') == digest(evidence)
    review = tmp_path / 'review.json'
    review.write_text('{"confirmed":true}')
    archive.add(review, Path('reviews') / review.name, 'later_review')
    archive.seal('Fixture', 'stateful', {})
    receipt_hash = read(archive.public / 'manifest.json')['private_archive']['inventory_sha256']
    assert 'reviews/review.json' in read(archive.private / 'inventory.json')
    (archive.private / 'inputs/evidence.xlsx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Evidence hash differs'):
        verify_inventory(archive.private, receipt_hash)


def test_scores_distinguish_new_fix_regression_inherited_and_missing():
    before = {'fixed': (False, 3), 'already_works': (True, 3), 'regression': (True, 2),
              'still_failing': (False, 2), 'missing': (True, 1)}
    after = {'fixed': (True, 3), 'already_works': (True, 3), 'regression': (False, 2),
             'still_failing': (False, 2)}
    summary = compare_results(before, after, 3)
    assert summary['new']['fail_to_pass'] == summary['new']['pass_to_pass'] == 1
    assert summary['retained']['pass_to_fail'] == summary['retained']['fail_to_fail'] == 1
    assert summary['retained']['pass_to_missing'] == 1
