"""Ensure the Agent input assembly cannot copy extra benchmark files."""

import pytest

from .hotel_agent import PUBLIC_NAMES, check_public, event_summary, file_hashes, prepare_workspace


def test_freeze_public_only_writable_copies(tmp_path):
    public = tmp_path / 'layout' / 'public'
    private = public.parent / 'private'
    public.mkdir(parents=True)
    private.mkdir()
    (private / 'oracle.json').write_text('{}', encoding='utf-8')
    for name in PUBLIC_NAMES:
        (public / name).write_bytes(name.encode())
    work = tmp_path / 'trial' / 'submission'
    prepare_workspace(public, work)
    assert file_hashes(public, PUBLIC_NAMES) == file_hashes(work, PUBLIC_NAMES)
    assert not (work / 'oracle.json').exists()
    (work / PUBLIC_NAMES[0]).write_bytes(b'edited')
    assert (public / PUBLIC_NAMES[0]).read_bytes() == PUBLIC_NAMES[0].encode()
    (public / 'unexpected-input.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        check_public(public)


def test_native_asset_symlink_cannot_expose_private_file(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    for name in PUBLIC_NAMES[1:]:
        (public / name).touch()
    oracle = tmp_path / 'oracle.json'
    oracle.touch()
    (public / PUBLIC_NAMES[0]).symlink_to(oracle)
    with pytest.raises(ValueError):
        prepare_workspace(public, tmp_path / 'run' / 'submission')


def test_runtime_failure_is_reported_separately_from_completed_usage(tmp_path):
    log = tmp_path / 'events.jsonl'
    log.write_text('\n'.join(['{"type":"thread.started","thread_id":"case-a"}',
                             '{"type":"item.completed","item":{"type":"error","message":"tool host unavailable"}}',
                             '{"type":"item.completed","item":{"type":"command_execution","exit_code":1}}',
                             '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
                             '{"type":"turn.failed","error":{"message":"network failure"}}']),
                   encoding='utf-8')
    summary = event_summary(log)
    assert summary['thread_ids'] == ['case-a']
    assert summary['usage'] == [{'input_tokens': 10, 'output_tokens': 5}]
    assert summary['tool_events'][0]['exit_code'] == 1
    assert [event['type'] for event in summary['runtime_events']] == ['item.completed', 'turn.failed']
