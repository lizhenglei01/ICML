"""Two releases, locked submissions and tamper-evident checkpoints, without score feedback."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from datetime import datetime,timezone

from medical_cf.run import save
from .build import digest
from .evaluate import validate_submission


def now():return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self,package,root):
        self.package=Path(package).resolve();self.root=Path(root).resolve()

    def _events(self,event):
        with (self.root/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(event,time=now()),ensure_ascii=False)+'\n')

    def start(self):
        self.root.mkdir(parents=True,exist_ok=True)
        if (self.root/'state.json').exists():raise FileExistsError('Session already exists; use a new session directory')
        shutil.copytree(self.package/'stage1',self.root/'stage1')
        manifest={str(p.relative_to(self.root/'stage1')):digest(p) for p in (self.root/'stage1').rglob('*') if p.is_file()}
        save(self.root/'state.json',{'phase':'stage1_open','input_manifest':manifest,'created':now()})
        self._events({'event':'stage1_released','files':list(manifest)})
        return self.root/'stage1'

    def state(self):return json.loads((self.root/'state.json').read_text())

    def _check_source(self,state):
        actual={str(p.relative_to(self.root/'stage1')):digest(p) for p in (self.root/'stage1').rglob('*') if p.is_file()}
        if actual!=state['input_manifest']:raise ValueError('Released stage1 source package changed')

    def commit(self,stage,submission):
        state=self.state();self._check_source(state)
        if state['phase']!=f'stage{stage}_open':raise ValueError('Stage closed or not yet released')
        goals=json.loads((self.root/'stage1/goals.json').read_text())
        validate_submission(submission,goals,stage)
        path=self.root/f'submission_stage{stage}.json'
        # Exclusive create; wrong answers can be committed, but not replaced after seeing stage2.
        with path.open('x',encoding='utf-8') as f:
            f.write(json.dumps(submission,ensure_ascii=False,indent=2)+'\n')
        state[f'stage{stage}_sha256']=digest(path)
        state['phase']=f'stage{stage}_locked'
        save(self.root/'state.json',state)
        self._events({'event':f'stage{stage}_committed','sha256':state[f'stage{stage}_sha256']})
        return path

    def reveal(self):
        state=self.state();self._check_source(state)
        if state['phase']!='stage1_locked':raise ValueError('Stage1 must be committed before revealing the change')
        if digest(self.root/'submission_stage1.json')!=state['stage1_sha256']:raise ValueError('Stage1 submission was modified')
        text=(self.package/'private/stage2_instruction.md').read_text()
        stage2=self.root/'stage2';stage2.mkdir()
        (stage2/'instruction.md').write_text(text)
        state['stage2_prompt_sha256']=digest(stage2/'instruction.md');state['phase']='stage2_open'
        save(self.root/'state.json',state)
        self._events({'event':'stage2_released','stage1_sha256':state['stage1_sha256'],'prompt_sha256':state['stage2_prompt_sha256']})
        return text

    def verify_locked(self):
        state=self.state();self._check_source(state)
        if state['phase']!='stage2_locked':raise ValueError('Both stages must be committed')
        for stage in (1,2):
            if digest(self.root/f'submission_stage{stage}.json')!=state[f'stage{stage}_sha256']:
                raise ValueError('Locked submission changed')
        if digest(self.root/'stage2/instruction.md')!=state['stage2_prompt_sha256']:raise ValueError('Stage2 prompt changed')
        return True


def main():
    import argparse
    from .evaluate import evaluate
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['start','submit','reveal','evaluate'])
    p.add_argument('--package',type=Path,required=True);p.add_argument('--session',type=Path,required=True)
    p.add_argument('--stage',type=int,choices=[1,2]);p.add_argument('--response',type=Path)
    a=p.parse_args();s=Session(a.package,a.session)
    if a.action=='start':print(s.start())
    elif a.action=='submit':
        if a.stage is None or a.response is None:p.error('submit needs --stage and --response')
        print(s.commit(a.stage,json.loads(a.response.read_text())))
    elif a.action=='reveal':s.reveal();print(s.root/'stage2/instruction.md')
    else:
        s.verify_locked()
        report=evaluate(s.package,s.root/'submission_stage1.json',s.root/'submission_stage2.json',s.root/'delivered')
        save(s.root/'evaluation.json',report)
        print(json.dumps({'pass':report['pass'],'summary':report['summary']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
