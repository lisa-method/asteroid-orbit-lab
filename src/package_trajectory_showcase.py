"""Copy small reviewed animation artifacts into the documentation allowlist."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def package(root, sources):
    destination=root/'docs/figures/trajectory_showcase'
    destination.mkdir(parents=True,exist_ok=True)
    entries=[]
    for source in sources:
        directory=root/source
        audit=json.loads((directory/'audit.json').read_text())
        verification=json.loads((directory/'verification.json').read_text())
        if verification.get('passed') is not True or verification['audit_sha256'] != sha(directory/'audit.json'):
            raise ValueError('verified current coordinates required')
        if audit['builder_sha256'] != sha(root/'src/build_trajectory_showcase.py') or verification['verifier_sha256'] != sha(root/'src/verify_trajectory_showcase.py'):
            raise ValueError('presentation source changed; rebuild/verify')
        name=directory.name
        files={}
        for scene,expected in audit['scene_sha256'].items():
            if sha(directory/(scene+'.json')) != expected:
                raise ValueError('scene changed')
            subprocess.run(['/private/tmp/verify_animation_media',str(directory/scene)],check=True)
            render=json.loads((directory/scene/'metadata.json').read_text())
            if render.get('scene_sha256') != expected or render.get('renderer_binary_sha256') != sha(Path('/private/tmp/render_trajectory_animation')):
                raise ValueError('rendered media do not match current scene/renderer; rerender')
            paths=[(directory/(scene+'.json'),f'{name}__{scene}.json'),
                   (directory/scene/'trajectory_animation.gif',f'{name}__{scene}.gif'),
                   (directory/scene/'frame_end.png',f'{name}__{scene}.png'),
                   (directory/scene/'metadata.json',f'{name}__{scene}__render.json')]
            for original,target in paths:
                if original.stat().st_size > 2_000_000:
                    raise ValueError('reviewed documentation figure unexpectedly exceeds 2 MB')
                shutil.copyfile(original,destination/target)
                files[target]=dict(sha256=sha(original),bytes=original.stat().st_size)
        for suffix in ['audit','verification']:
            original=directory/(suffix+'.json');target=name+'__'+suffix+'.json'
            shutil.copyfile(original,destination/target)
            files[target]=dict(sha256=sha(original),bytes=original.stat().st_size)
        entries.append(dict(name=name,object_id=audit['object_id'],source=audit['source'],files=files))
    result=dict(schema_version=1,scope='small derived illustrations, not raw datasets',entries=entries,
                source_hashes={p:sha(root/p) for p in ['configs/trajectory_showcase.json','src/run_trajectory_showcase.py','src/build_trajectory_showcase.py','src/verify_trajectory_showcase.py','src/render_trajectory_animation.swift','src/verify_animation_media.swift','src/package_trajectory_showcase.py']})
    (destination/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(directory=str(destination),objects=len(entries),bytes=sum(f['bytes'] for e in entries for f in e['files'].values()))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sources',nargs='+',type=Path);a=p.parse_args()
    package(Path.cwd(),a.sources)
