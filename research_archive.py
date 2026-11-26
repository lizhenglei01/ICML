"""Archive benchmark evidence locally and publish an allowlisted audit index."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from shutil import copyfile
import subprocess
import sys
import tarfile
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'experiments'
PRIVATE = ROOT / 'artifacts_research_archive'
LOGIC = Path('multifile_reconstruction/reasoning_graph_mvp/logic_first')
ASSETS = ROOT / LOGIC / 'business_artifacts'
LABEL = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.\-]*')
BUILDERS = {'stateful': 'hotel_stateful', 'workflow': 'hotel_workflow', 'routing': 'hotel_dependency_scope',
            'upgrade': 'hotel_upgrade'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def capture_run_environment(kind):
    """Record live provenance before a newly instrumented model trial starts."""
    from importlib.metadata import version
    from shutil import which

    stem = BUILDERS[kind]
    def tool_version(tool):
        binary = which(tool)
        if not binary:
            return None
        result = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=15)
        return result.stdout.splitlines()[0] if result.returncode == 0 and result.stdout else None

    names = (
        'hotel_agent.py', f'{stem}_agent.py',
        'hotel_stateful_bench.py' if kind == 'stateful' else f'{stem}_bench.py')
    if kind == 'routing':
        names = ('hotel_agent.py', f'{stem}_agent.py', 'hotel_dependency_scope.py')
    support = ('hotel_stateful_reference.py',) if kind == 'stateful' else ('hotel_office.py', 'hotel_milestone_formulas.py')
    if kind == 'workflow':
        support += ('hotel_workflow_cases.py',)
    if kind == 'upgrade':
        support = ('hotel_upgrade_cases.py', 'hotel_upgrade_reference.py', 'hotel_stateful_reference.py',
                   'hotel_stateful_bench.py', 'hotel_stateful_agent.py')
    return {'git_commit': git('rev-parse', 'HEAD'), 'dirty_tree': bool(git('status', '--porcelain')),
            'python': sys.version.split()[0], 'openpyxl': version('openpyxl'),
            'codex_cli': tool_version('codex'), 'bubblewrap': tool_version('bwrap'),
            'libreoffice': tool_version('libreoffice') if kind != 'stateful' else None, 'seed': None,
            'source_sha256': {str(LOGIC / name): digest(ROOT / LOGIC / name) for name in (*names, *support)}}


class Archive:
    """Keep exact file copies separate from structured public metadata."""

    def __init__(self, label, run_id, public_root=PUBLIC, private_root=PRIVATE):
        if not LABEL.fullmatch(label) or not LABEL.fullmatch(run_id):
            raise ValueError('Experiment label and run ID must be single safe path components')
        self.label, self.run_id = label, run_id
        self.public = public_root / label
        self.private = private_root / run_id
        private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.private.mkdir(exist_ok=True, mode=0o700)
        self.files = {}

    def add(self, source, relative, role):
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts or source.is_symlink() or not source.is_file():
            raise ValueError(f'Expected a regular evidence file for {relative}')
        target = self.private / relative
        fingerprint = digest(source)
        if target.is_symlink() or (target.exists() and digest(target) != fingerprint):
            raise ValueError(f'Immutable evidence differs for {absolute_label(self, relative)}')
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            copyfile(source, target)
            target.chmod(0o600)
        if digest(target) != fingerprint:
            raise ValueError(f'Evidence changed while copying {relative}')
        self.files[relative.as_posix()] = {'role': role, 'sha256': fingerprint, 'bytes': target.stat().st_size}

    def tree(self, source, relative, role, *, allowed=None):
        for path in sorted(source.rglob('*')):
            if path.is_file() and not path.is_symlink() and (allowed is None or path.suffix in allowed):
                self.add(path, Path(relative) / path.relative_to(source), role)

    def derived(self, relative, data, role):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp:
            path = Path(temp) / 'evidence.json'
            write(path, data)
            self.add(path, relative, role)

    def seal(self, title, family, provenance):
        receipt = self.private / 'inventory.json'
        if receipt.exists():
            published = read(receipt)
            if any(self.files.get(name) != record for name, record in published.items()):
                raise ValueError(f'Previously sealed evidence changed for {self.run_id}')
        write(receipt, self.files)
        receipt.chmod(0o600)
        roles = Counter(item['role'] for item in self.files.values())
        write(self.public / 'manifest.json', {
            'schema_version': 1, 'label': self.label, 'run_id': self.run_id, 'title': title,
            'family': family, 'provenance': provenance,
            'private_archive': {'location': f'artifacts_research_archive/{self.run_id}/',
                                'inventory': 'inventory.json', 'inventory_sha256': digest(receipt),
                                'files': len(self.files), 'roles': dict(roles),
                                'anchor_files': {path: item['sha256'] for path, item in self.files.items()
                                                 if Path(path).name in {'oracle.json', 'instruction.md',
                                                                        'source_snapshot.tar', 'raw_summary.json',
                                                                        'scoring.json', 'results.json'}}},
        })


def absolute_label(archive, path):
    return f'{archive.run_id}/{path}'


def verify_inventory(path, expected_digest=None):
    receipt = path / 'inventory.json'
    if expected_digest and digest(receipt) != expected_digest:
        raise ValueError('Private inventory hash differs from the public manifest')
    for name, record in read(receipt).items():
        relative = Path(name)
        target = path / relative
        if relative.is_absolute() or '..' in relative.parts or target.is_symlink() or not target.is_file():
            raise ValueError(f'Invalid archived path: {name}')
        if target.stat().st_size != record['bytes'] or digest(target) != record['sha256']:
            raise ValueError(f'Evidence hash differs: {name}')


def compare_results(before, after, stage):
    """Count explicit transitions for a fixed case set, retaining baseline failures."""
    out = {}
    for scope in ('new', 'retained'):
        keys = {key for key, (_, version) in before.items() if (version == stage) == (scope == 'new')}
        counts = Counter(('pass' if before[key][0] else 'fail') + '_to_' +
                         ('missing' if key not in after else 'pass' if after[key][0] else 'fail') for key in keys)
        out[scope] = {'count': len(keys), **{name: counts[name] for name in
                       ('pass_to_pass', 'pass_to_fail', 'fail_to_pass', 'fail_to_fail', 'pass_to_missing', 'fail_to_missing')}}
    return out


def flags(payload, kind, origins):
    if kind in ('stateful', 'upgrade'):
        return {row['name']: (row['pass'], row['introduced']) for row in payload.get('cases', [])}
    return {row['object']: (row['pass'], origins[row['object']]) for row in payload.get('checks', [])
            if row['object'] in origins}


def score_source(run, label):
    primary = run / label / 'native_evaluation.json'
    return primary if primary.exists() else run / label / 'submitted_files_rescore.json'


def file_differences(before, after, files, kind):
    if kind in ('stateful', 'upgrade'):
        import ast
        from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_stateful_bench import read_state

        def functions(folder):
            parsed = ast.parse((folder / 'approval.py').read_text(encoding='utf-8'))
            return {n.name: ast.dump(n) for n in parsed.body if isinstance(n, ast.FunctionDef)}

        old, new = functions(before), functions(after)
        return {'workbook_seed_changed': read_state(before / 'approval.xlsx') != read_state(after / 'approval.xlsx'),
                'report_templates_changed': [name for name in files if Path(name).suffix in {'.docx', '.pptx'}
                                             and digest(before / name) != digest(after / name)],
                'functions_added': sorted(new.keys() - old.keys()),
                'functions_modified': sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
                'functions_removed': sorted(old.keys() - new.keys())}
    from multifile_reconstruction.revision import snapshot

    old, new = snapshot(before, tuple(files)), snapshot(after, tuple(files))
    changed = [key for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
    return {'objects_changed_by_file': dict(Counter(key.partition(':')[0] for key in changed)),
            'object_keys': changed}


def stage_number(label):
    return int(re.match(r'm(\d+)', label).group(1))


def actual_input(package, run, label):
    for folder in ('carried', 'carried_inputs'):
        candidate = run / folder / label / 'public'
        if candidate.is_dir():
            return candidate
    return package / 'public'


def grade_before(folder, oracle, stage, kind):
    if kind == 'upgrade':
        from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_upgrade_bench import grade
        return grade(folder, oracle)
    if kind == 'stateful':
        from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_stateful_bench import grade
        return grade(folder, stage, oracle)
    from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_office import grade
    return grade(folder, oracle)


def hotel(kind, title, label, source, run, source_review=None):
    archive = Archive(label, run.name)
    plan = read(run / 'plan.json')
    records = read(run / 'results.json')
    execution, tests, regressions = {}, {}, {}
    archive.add(run / 'results.json', 'run/results.json', 'execution')
    archive.add(run / 'plan.json', 'run/plan.json', 'environment')
    for name in ('comparison.json', 'comparison_metrics.json', 'paired_input_audit.json',
                 'business_oracle.json', 'business_rescore.json'):
        if (run / name).is_file():
            archive.add(run / name, f'run/{name}', 'original_or_review')
    archive.tree(source / 'controls', 'controls', 'fault_controls', allowed={'.json', '.py', '.xlsx', '.docx', '.pptx'})
    if (source / 'summary.json').is_file():
        archive.add(source / 'summary.json', 'build/summary.json', 'construction')
    for name, record in records.items():
        stage = stage_number(name)
        package = source / (f'm{stage}' if kind == 'stateful' else name)
        before = actual_input(package, run, name)
        after = run / name / 'submission'
        private = package / 'private'
        execution[name] = {key: record.get(key) for key in
                           ('model', 'effort', 'starting_point', 'status', 'wall_seconds')}
        execution[name]['stage'] = stage
        start_hashes = record.get('public_sha256') or {}
        end_hashes = record.get('submission_sha256') or {}
        for folder, hashes, role in ((before, start_hashes, 'input'), (after, end_hashes, 'submission')):
            for file, wanted in hashes.items():
                file = Path(file)
                if len(file.parts) != 1 or digest(folder / file) != wanted:
                    raise ValueError(f'{name} {role} does not match the recorded hash')
                archive.add(folder / file, Path(name) / role / file, role)
        for file in ('prompt.txt', 'events.jsonl', 'stderr.log', 'run.json', 'completion/completion.txt',
                     'integration_trace.json', 'business_evaluation.json'):
            if (run / name / file).is_file():
                archive.add(run / name / file, f'{name}/execution/{file}', 'trace_or_review')
        if not private.exists() or not score_source(run, name).is_file():
            tests[name] = {'score_available': False, 'status': record.get('status')}
            continue
        oracle_source = private / 'oracle.json'
        oracle = read(oracle_source)
        archive.tree(private, f'{name}/grader', 'oracle_reference_and_build',
                     allowed={'.json', '.py', '.xlsx', '.docx', '.pptx'})
        after_score = read(score_source(run, name))
        archive.add(score_source(run, name), f'{name}/evaluation/scoring.json', 'original_evaluation')
        origins = read(private / 'milestone.json').get('formula_check_stage', {}) if kind not in ('stateful', 'upgrade') else {}
        replay_path = f'{name}/evaluation/baseline_replay.json'
        if (archive.private / replay_path).is_file():
            archive.add(archive.private / replay_path, replay_path, 'retrospective_baseline')
            baseline = read(archive.private / replay_path)
        else:
            baseline = grade_before(before, oracle, stage, kind)
            archive.derived(replay_path, baseline, 'retrospective_baseline')
        before_flags, after_flags = flags(baseline, kind, origins), flags(after_score, kind, origins)
        tests[name] = {'original_pass': after_score['pass'], 'score_available': True,
                       'units': 'action sequence' if kind in ('stateful', 'upgrade') else 'formula assertion',
                       'baseline_status': baseline.get('status', 'native_grade'),
                       'checks_passed': sum(ok for ok, _ in after_flags.values()), 'checks_total': len(after_flags)}
        if kind in ('stateful', 'upgrade'):
            tests[name].update({key: record.get(key) for key in ('step_count', 'step_pass_count')})
        if kind == 'upgrade':
            tests[name].update({key: record.get(key) for key in ('matched_cases', 'expanded_only')})
        if (run / name / 'business_evaluation.json').is_file():
            tests[name]['business_rescore_pass'] = bool(read(run / name / 'business_evaluation.json')['pass'])
        regressions[name] = compare_results(before_flags, after_flags, stage) if before_flags else {'baseline_available': False}
        if after.is_dir() and end_hashes:
            try:
                diff = file_differences(before, after, list(end_hashes), kind)
            except (BadZipFile, OSError, ValueError, KeyError, SyntaxError) as exc:
                diff = {'status': 'diff_unavailable', 'error_type': type(exc).__name__}
            archive.derived(f'{name}/evaluation/diff.json', diff, 'object_or_code_changes')
            execution[name]['change_summary'] = {key: value for key, value in diff.items() if key != 'object_keys'}
    dependencies = dependency_report(kind, archive, run)
    write(archive.public / 'execution_summary.json', execution)
    write(archive.public / 'test_summary.json', tests)
    write(archive.public / 'regression_report.json', {'baseline': 'retrospective replay of recorded pre-submission input',
                                                     'after': 'preserved original score', 'by_stage': regressions})
    write(archive.public / 'dependency_audit.json', dependencies)
    task_specs(archive.public, kind)
    archive.seal(title, kind, {'recorded_at_run': plan.get('environment_at_run'),
                               'source_review_commit': source_review,
                               'baseline_replay_commit': git('rev-parse', 'HEAD'),
                               'note': ('Historical run has no recorded project commit or tool versions.'
                                        if 'environment_at_run' not in plan else None)})
    return archive


def dependency_report(kind, archive, run):
    if kind == 'upgrade':
        checks = {}
        for name in ('m3_narrow', 'm3_expanded'):
            path = score_source(run, name)
            if path.is_file():
                groups = ('refund_release_gate', 'signing_before_refund', 'reversal_after_slot_taken',
                          'withdraw_and_reassign', 'cross_month_publish_versions')
                checks[name] = {group: {'passed': sum(row['pass'] for row in rows), 'total': len(rows)}
                                for group in groups if (rows := [row for row in read(path)['cases'] if row['scenario'] == group])}
        summary = {'runs': checks, 'scope': 'Matched inputs and existing operations; expanded branch adds operations and interactions.',
                   'comparison_limitation': 'The two treatments differ in both functional scope and dependencies.'}
        review = run / 'business_rescore.json'
        if review.is_file():
            summary['diagnostic_rescore'] = read(review)
        return summary
    if kind == 'stateful':
        summary = {}
        for name in ('m3_continuous', 'm3_independent'):
            path = score_source(run, name)
            if path.is_file():
                scores = {case['name']: case['pass'] for case in read(path).get('cases', [])}
                summary[name] = {key: scores.get(key) for key in
                                 ('refund_with_release_blocked', 'sign_then_refund', 'refund_then_attempt_to_sign')}
        return {'checked': 'remove persisted release and attempt promotion; execute signing and refund in both orders',
                'runs': summary, 'scope': 'actual action traces; no claim about untested operations'}
    if kind == 'workflow':
        from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_milestone_formulas import verify_formula_function

        traces = {}
        for name in ('m3_manual', 'm3_linked'):
            path = run / name / 'integration_trace.json'
            if path.is_file():
                traces[name] = {row['name']: row['pass'] for row in read(path)['scenarios']}
        test = run / 'm3_linked/submission/hotel_review.xlsx'
        if test.is_file():
            initial = {('Review', 'AA2'): 1, ('Review', 'AC3'): 1, ('Review', 'AD3'): 2}
            trace = verify_formula_function(test, [
                {'name': 'current', 'inputs': {}, 'expected': initial},
                {'name': 'block_release', 'inputs': {('Review', 'AA2'): 0},
                 'expected': {('Review', 'AC3'): 1, ('Review', 'AD3'): 2}},
            ], require_sensitivity=False)
            archive.derived('audit/release_block.json', trace, 'dependency_intervention')
            blocked = 'bypass_observed' if trace['pass'] else 'review_required'
        else:
            blocked = 'not_run'
        return {'trace_checks': traces, 'release_to_promotion': blocked,
                'scope': 'The release flag and promotion were computed separately in this fixture.'}
    rescore = run / 'business_rescore.json'
    if not rescore.is_file():
        return {'scope': 'Centralized versus local routing. No persisted action replay.'}
    return {'scope': 'Centralized versus local routing. No persisted action replay.',
            'scoring_revision': {arm: {'original_pass': row['strict_pass'], 'business_pass': row['business_pass'],
                                       'removed_nonapplicable_failed_checks': len(row['ignored_original_checks'])}
                                 for arm, row in read(rescore)['arms'].items()}}


def task_specs(folder, kind):
    specs = {
        'stateful': [('建立申请和正式签发', 2), ('释放名额、排候补并递补', 3), ('接入退款并复测旧操作', 1)],
        'workflow': [('经营底稿', None), ('审批与发布', None), ('退款与续约', None)],
        'routing': [('经营计算', None), ('新签审批', None), ('续约政策路由', None)],
        'upgrade': [('建立申请和正式签发', 2), ('释放名额、排候补并跨文件发布', 3),
                    ('同一前序下的窄版退款和扩大版多业务升级', None)],
    }
    text = '# 任务范围\n\n'
    for stage, (name, count) in enumerate(specs[kind], 1):
        text += f'- M{stage} {name}'
        text += f'。新增 {count} 个可调用操作，兼查既有操作' if count is not None else ''
        text += '。\n'
    if kind == 'workflow':
        text += '\n每轮登记 10 个结果列。该口径包含中间计算。\n'
    if kind == 'upgrade':
        text += '\nM3 窄版新增一个操作；扩大版新增五个。两组共用基础测试，扩大版另测新流程衔接。\n'
    text += '\n构建者指定业务条件。实际输入、原提示词、测试场景和标准答案见私有清单。\n'
    target = folder / 'task_specs/README.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')


def nav_counts(row):
    return {key: value for key, value in row.items()
            if key.startswith(('fail_to_pass_', 'pass_to_pass_', 'none_to_pass_')) and type(value) is int}


def source_delta(previous, checkpoint):
    def go_hashes(path):
        with tarfile.open(path) as archive:
            return {member.name: hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
                    for member in archive if member.isfile() and member.name.endswith('.go')
                    and not member.name.endswith('_test.go')}
    before, after = go_hashes(previous), go_hashes(checkpoint)
    return {'go_source_added': len(after.keys() - before.keys()),
            'go_source_removed': len(before.keys() - after.keys()),
            'go_source_modified': sum(before[key] != after[key] for key in before.keys() & after.keys())}


def navidrome():
    benchmark = ROOT / 'artifacts/swe_milestone_gpt62'
    upstream = benchmark / 'data/navidrome_navidrome_v0.57.0_v0.58.0'
    run = upstream / 'e2e_trial/codex_gpt-6.2-sol_navidrome_001'
    archive = Archive('swe_milestone_navidrome', run.name)
    scores = run / 'evaluation'
    archive.add(scores / 'summary_filtered.json', 'evaluation/raw_summary.json', 'original_evaluation')
    archive.add(scores / 'summary.json', 'evaluation/unfiltered_summary.json', 'original_evaluation')
    raw = read(scores / 'summary_filtered.json')
    stats = read(run / 'agent_stats.json')
    for file in ('agent_stats.json', 'trial_metadata.json', 'repo_config.yaml', 'runtime_policy.yaml', 'e2e_config.yaml',
                 'dependencies.csv', 'milestones.csv', 'selected_milestone_ids.txt'):
        archive.add(run / file, f'run/{file}', 'environment_or_task_design')
    for file in ('agent_prompt.txt', 'agent_stdout.txt', 'agent_stderr.txt'):
        archive.add(run / 'log' / file, f'run/trace/{file}', 'trace')
    tests, execution = {}, {}
    previous = None
    for name, row in raw['results'].items():
        original = upstream / 'srs' / name / 'SRS.md'
        supplied = run / 'e2e_workspace/srs' / f'{name}_SRS.md'
        if digest(original) != digest(supplied):
            raise ValueError(f'Archived SRS and agent SRS differ for {name}')
        archive.add(supplied, f'{name}/input/SRS.md', 'input')
        classification = upstream / 'test_results' / name / f'{name}_classification.json'
        if classification.is_file():
            archive.add(classification, f'{name}/grader/classification.json', 'oracle')
        for file in ('source_snapshot.tar', 'source_snapshot.integrity.json', 'evaluation_result.json',
                     'evaluation_result_filtered.json', 'feedback_report.md'):
            if (scores / name / file).is_file():
                archive.add(scores / name / file, f'{name}/evaluation/{file}',
                            'submission' if file == 'source_snapshot.tar' else 'original_evaluation')
        snapshot_path = scores / name / 'source_snapshot.tar'
        execution[name] = {'model': 'gpt-6.2-sol', 'harness': 'Codex', 'eval_status': row['eval_status'],
                           'submitted_commit': row['tag_hash'], 'starting_point': 'continuous_repository',
                           'grading_timestamp': row['timestamp']}
        recorded = stats['milestone_stats'].get(name, {})
        execution[name].update({key: recorded.get(key) for key in ('duration_ms', 'wall_clock_ms', 'total_tool_calls')})
        if previous:
            execution[name]['changes_from_previous_submitted_checkpoint'] = source_delta(previous, snapshot_path)
        previous = snapshot_path
        tests[name] = nav_counts(row['test_summary'])
    corrected_path = benchmark / 'navidrome_003_sub-02_corrected_20261123T175253'
    corrected = read(corrected_path / 'evaluation_result.json')
    archive.tree(corrected_path, 'reviews/multi_library_corrected', 'diagnostic_replay',
                 allowed={'.json', '.log', '.txt'})
    historical_path = benchmark / 'navidrome_historical_20261123T181353'
    archive.tree(historical_path, 'reviews/historical', 'historical_replay', allowed={'.json'})
    browser = benchmark / 'navidrome_user_edit_20261123T200743'
    archive.tree(browser, 'reviews/user_edit', 'browser_reproduction', allowed={'.json', '.log', '.png'})
    historical = read(historical_path / 'historical_comparison_with_prior_replay.json')
    replay = {name: {'previously_passing_matched': row['previously_passing_matched'],
                     'previously_passing_unverified': len(row['previously_passing_unverified']),
                     'candidate_regressions': len(row['regression_candidates']),
                     'repeated_failures': len(row['repeated_failures'])}
              for name, row in historical['results'].items()}
    browser_checks = {name: {'save_and_reload_pass': read(browser / name / 'controlled/browser_result.json')['passed']}
                      for name in ('milestone_001', 'milestone_004')}
    write(archive.public / 'execution_summary.json', {'order': list(raw['results']), 'milestones': execution,
        'reasoning_effort': stats['reasoning_effort'], 'wall_clock_ms': stats['summary']['wall_clock_ms']})
    write(archive.public / 'test_summary.json', {'original': raw['statistics'], 'by_milestone': tests,
        'corrected_replay_003_sub_02': nav_counts(corrected['test_summary'])})
    write(archive.public / 'regression_report.json', {'historical_test_replay': replay, 'browser_checks': browser_checks,
        'interpretation': 'A failed old fixture may indicate incompatibility. The reproduced user editing path still saved.'})
    write(archive.public / 'dependency_audit.json', {'scope': 'Official milestone DAG plus post-run behavior diagnosis',
        'cross_library_move': 'Submitted code skipped cross-library lookup when the scan state listed one library.',
        'cleanup': 'The replacement repository API panicked in the official test mock. Production cleanup was not independently reproduced.',
        'controlled_dependency_blocking': 'not_run'})
    target = archive.public / 'task_specs/README.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# Navidrome 任务范围\n\n九个里程碑沿上游依赖队列推进。多曲库服务与扫描包含 11 类功能要求。实际 SRS、运行配置和测试分类见私有清单。\n',
                      encoding='utf-8')
    archive.seal('SWE-Milestone Navidrome 九轮对照', 'navidrome', {
        'upstream_commit': '17a8f1593e172e26b36cea15e2b30fb9536c93f5',
        'model': 'gpt-6.2-sol', 'reasoning_effort': stats['reasoning_effort'], 'prototype_git_commit': None,
        'corrected_replay_image_id': corrected['evaluation_environment']['offline_cache_effective_image_id'],
        'note': 'Milestone snapshots have exact submission SHAs. The local diagnostic scripts were outside the project Git history.',
    })
    return archive


def write_analysis(archive, kind):
    content = {
        'stateful': """# 有状态酒店审批

GPT‑6.2‑sol 的连续组继承实际可读提交，独立组使用标准前序。最终两组各通过 11 条操作序列和 58 个检查步骤。逐次调用同一命令并重开同一工作簿，验证财务状态、签发快照和名额记录。

阻断已保存的释放记录后，实际提交会拒绝递补。签发与退款的先后顺序改变了后续审批结果。预先构造的三种错误分别被检出。本次只覆盖人工构造的酒店原型，未观察到自然发生的新功能遗漏或旧功能回归。
""",
        'workflow': """# 酒店公式工作流

三轮各有 10 个输出列。模型两组最终都通过。后续检查发现，模型保留了前两轮的 80 个公式单元格，第三轮在新列实现退款和重审。

阻断模型工作簿里的释放标志后，候补仍能依靠重排名得到名额。这个结果揭示了设计边界，也解释了后续为什么改用可保存的释放记录和实际操作重放。
""",
        'routing': """# 酒店路由对照及复核

保留原始评分与另行的业务复核。B 组原评分有 16 项未通过，都属于业务上无需走续约分支时的中间值。复核使用同一批提交，两组业务检查均通过。

这次的评分调整保留了旧功能、三个入口和发布产物的检查。复核结果应与原结果一并引用。
""",
        'navidrome': """# Navidrome 九轮对照

官方过滤后为 3 轮通过、6 轮未通过。该数字包含测试环境和测试覆盖问题。对多曲库服务与扫描的同一提交修正镜像中重复迁移后，新增测试通过 83/95。跨库匹配和访问判断出现行为不符。清理相关两个用例在模拟组件接口处异常，实际清理未独立复现。

后期旧用户编辑测试曾失败。受控浏览器检查对早期和最终提交均成功保存并重新加载。证据支持的是该实际操作路径。九轮原始提交、评分、日志与诊断重放分开归档。
""",
    }
    target = archive.public / 'analysis.md'
    if not target.exists():
        historical = {'hotel_workflow_002', 'hotel_workflow_001', 'hotel_policy_routing', 'swe_milestone_navidrome'}
        if archive.label in historical:
            text = content[kind]
        else:
            tests = read(archive.public / 'test_summary.json')
            lines = ['# 本轮实验记录', '', '| 阶段 | 原始评分 |', '|---|---|']
            for name, record in tests.items():
                outcome = ('通过' if record['original_pass'] else '未通过') if record['score_available'] else '未评分'
                lines.append(f'| {name} | {outcome} |')
            lines.extend(['', '实际起点、连续性、旧功能改动和依赖干预见本目录的运行与诊断摘要。'])
            text = '\n'.join(lines) + '\n'
        target.write_text(text, encoding='utf-8')


def refresh_docs():
    manifests = [read(path) for path in sorted(PUBLIC.glob('*/manifest.json'))]
    index = ['| 实验 | Run ID | 证据 |', '|---|---|---|']
    summary = ['| 实验 | 最新归档结果 |', '|---|---|']
    open_checks = []
    managed_labels = set()
    for item in manifests:
        label, kind = item['label'], item['family']
        test_path = PUBLIC / label / 'test_summary.json'
        if kind not in {*BUILDERS, 'navidrome', 'automatic_business'} or not test_path.is_file():
            # Other experiment families keep their curated index entries.
            continue
        managed_labels.add(label)
        index.append(f"| {item['title']} | `{item['run_id']}` | [公开清单](../experiments/{label}/manifest.json) |")
        test = read(test_path)
        if kind == 'navidrome':
            original = test['original']
            outcome = f"原始评分 {original['passed']} 轮通过，{original['failed']} 轮失败"
            open_checks.append(f'- [{item["title"]}](../experiments/{label}/analysis.md) 保留覆盖不完整和测试接口异常的诊断。')
        elif kind == 'automatic_business':
            cases = test['cases']
            layouts = sum(len(case['layouts']) for case in cases.values())
            outcome = f'{len(cases)} 个业务案例，{layouts} 组原生文件与 Oracle 通过验证'
            open_checks.append(f'- [{item["title"]}](../experiments/{label}/analysis.md) 两组使用合成政策，难度以已登记规则为界。')
        else:
            scored = [row for row in test.values() if row['score_available']]
            passing = sum(row['original_pass'] for row in scored)
            outcome = f'原始评分 {passing}/{len(scored)} 次运行通过'
            if passing < len(scored):
                open_checks.append(f'- [{item["title"]}](../experiments/{label}/analysis.md) 有原始未通过记录，诊断和复核另存。')
        summary.append(f"| [{item['title']}](../experiments/{label}/analysis.md) | {outcome} |")
    registry = (ROOT / 'docs' / 'EXPERIMENT_REGISTRY.md').read_text(encoding='utf-8')
    for line in registry.partition('<!-- archive-start -->')[2].partition('<!-- archive-end -->')[0].splitlines():
        linked = re.search(r'\.\./experiments/([^/]+)/', line)
        if line.startswith('| ') and linked and linked[1] not in managed_labels:
            index.append(line)
    anchors = {'EXPERIMENT_REGISTRY.md': index, 'PROJECT_STATUS.md': summary, 'KNOWN_ISSUES.md': open_checks}
    for name, lines in anchors.items():
        path = ROOT / 'docs' / name
        if path.is_file():
            start, end = '<!-- archive-start -->', '<!-- archive-end -->'
            old = path.read_text(encoding='utf-8')
            prefix, _, suffix = old.partition(start)
            if start not in old or end not in suffix:
                raise ValueError(f'Archive markers missing in {name}')
            path.write_text(prefix + start + '\n\n' + '\n'.join(lines) + '\n\n' + end + suffix.partition(end)[2],
                            encoding='utf-8')


def archive_hotel_run(kind, source, run, *, label=None, title=None, source_review=None):
    titles = {'stateful': '有状态酒店审批', 'workflow': '酒店公式工作流', 'routing': '酒店政策路由对照',
              'upgrade': '酒店业务升级范围对照'}
    archive = hotel(kind, title or titles[kind], label or run.name, source, run, source_review)
    write_analysis(archive, kind)
    refresh_docs()
    return archive


def archive_automatic_business(run):
    """Publish validation counts and seal the private generated task packages."""
    archive = Archive('automatic_business_construction', run.name)
    title = '酒店与采购端到端自动构建'
    verified = run / 'verified'
    cases = {}
    all_threads = set()
    for case in sorted(verified.iterdir()):
        summary = read(case / 'summary.json')
        if not summary['passed']:
            raise ValueError(f'Unverified generated case: {case.name}')
        config = read(case / 'input.json')
        stage_audits = []
        for call_path in sorted((case / 'proposals').glob('attempt_*/*/call.json')):
            call = read(call_path)
            audit = read(call_path.parent / 'transport' / 'transport_audit.json')
            start = next(row['params'] for row in audit['wire_requests'] if row['method'] == 'thread/start')
            requests = audit['thread_start_results']
            request = read(call_path.parent / 'request.json')
            messages = request['messages']
            given = json.loads(messages[1]['content'])
            if (len(messages) != 2 or not start['ephemeral'] or not audit['host_tools_disabled']
                    or not audit['apps_disabled'] or len(requests) != 1 or requests[0]['instructionSources']
                    or call['thread_reused'] or call['tool_events'] or call['thread_id'] in all_threads
                    or given['business_topic'] != config['topic'] or given['business_goal'] != config['goal']
                    or (call_path.parent.name == 'observations' and given['available'])):
                raise ValueError(f'Generation isolation audit failed for {call_path}')
            all_threads.add(call['thread_id'])
            stage_audits.append({'stage': call_path.parent.name, 'model': call['reported_model'],
                                 'fresh_isolated_thread': True, 'seconds': call['wall_seconds']})
        if {row['stage'] for row in stage_audits} != {'observations', 'forward', 'backward', 'policy'}:
            raise ValueError(f'Incomplete proposal stages for {case.name}')
        cases[case.name] = {'topic': config['topic'], 'goal': config['goal'], 'seed': config['seed'],
                            'model_calls': stage_audits, 'search': summary['basis'],
                            'layouts': {key: {k: row[k] for k in ('minimum_files', 'minimum_depth',
                                                                   'insufficient_subsets', 'witnessed_subsets',
                                                                   'reference_grade', 'controls')}
                                        for key, row in summary['layouts'].items()}}
        archive.tree(case, Path('verified') / case.name, 'validated_build')
        for rejected in sorted((run / case.name / 'proposals').glob('attempt_*/rejection.json')):
            archive.add(rejected, Path('development_diagnostics') / case.name / rejected.parent.name / rejected.name,
                        'development_rejection')
    opens = read(run / 'office_open_check.json')
    archive.add(run / 'office_open_check.json', Path('office_open_check.json'), 'office_open_check')
    regression = run / 'regression_check.json'
    if regression.is_file():
        archive.add(regression, Path('regression_check.json'), 'regression_check')
    write(archive.public / 'test_summary.json', {'cases': cases, 'office_open_check': opens,
                                                 'regression': read(regression) if regression.is_file() else None,
                                                 'distinct_model_threads': len(all_threads)})
    source = ('scenario_author.py', 'scenario_graph.py', 'scenario_bench.py',
              'hotel_visibility.py', 'hotel_office.py', 'test_scenario_bench.py')
    archive.seal(title, 'automatic_business', {
        'base_commit': git('rev-parse', 'HEAD'),
        'source_sha256': {str(LOGIC / name): digest(ROOT / LOGIC / name) for name in source},
        'author_context': 'Fresh audited threads. Recorded structured context between stages. No evaluated Agent calls.',
    })
    manifest = read(archive.public / 'manifest.json')
    verify_inventory(archive.private, manifest['private_archive']['inventory_sha256'])
    refresh_docs()
    return archive


def backfill():
    for kind, folder, run_id, label, commit in [
        ('routing', 'hotel_dependency_scope', 'gpt62_scope_20261123T124429168003Z', 'hotel_policy_routing', '8268e64'),
        ('workflow', 'hotel_workflow_complexity', 'gpt62_workflow_20261123T134800813601Z', 'hotel_workflow_001', '8268e64'),
        ('stateful', 'hotel_stateful_actions', 'gpt62_stateful_20261123T150141302533Z', 'hotel_workflow_002', 'bd7bf30'),
    ]:
        source = ASSETS / folder
        archive = archive_hotel_run(kind, source, source / 'agent_runs' / run_id, label=label, source_review=commit)
        print('ARCHIVED', archive.label, len(archive.files), flush=True)
    archive = navidrome()
    write_analysis(archive, 'navidrome')
    refresh_docs()
    print('ARCHIVED', archive.label, len(archive.files), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='command', required=True)
    modes.add_parser('backfill')
    verify = modes.add_parser('verify')
    verify.add_argument('label')
    custom = modes.add_parser('hotel')
    custom.add_argument('kind', choices=BUILDERS)
    custom.add_argument('--source', type=Path, required=True)
    custom.add_argument('--run', type=Path, required=True)
    automatic = modes.add_parser('automatic-business')
    automatic.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'backfill':
        backfill()
    elif args.command == 'hotel':
        archive_hotel_run(args.kind, args.source.resolve(), args.run.resolve())
    elif args.command == 'automatic-business':
        archive = archive_automatic_business(args.run.resolve())
        print('ARCHIVED AND VERIFIED', archive.label, len(archive.files))
    else:
        if not LABEL.fullmatch(args.label):
            raise ValueError('Invalid experiment label')
        manifest = read(PUBLIC / args.label / 'manifest.json')
        verify_inventory(PRIVATE / manifest['run_id'], manifest['private_archive']['inventory_sha256'])
        print('VERIFIED', args.label, manifest['private_archive']['files'])


if __name__ == '__main__':
    main()
