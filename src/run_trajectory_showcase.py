"""Rebuild all post-hoc gallery scenes and media, leaving visual review explicit."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    config_path = root / 'configs/trajectory_showcase.json'
    config = json.loads(config_path.read_text())
    renderer = Path('/private/tmp/render_trajectory_animation')
    media_verifier = Path('/private/tmp/verify_animation_media')
    if not renderer.is_file() or not media_verifier.is_file():
        raise RuntimeError('Compile both Swift tools first; see docs/TRAJECTORY_SHOWCASE.md')
    entries = []
    for case in config['cases']:
        name = case['directory']
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('gallery directory must be one safe path component')
        output = root / 'outputs/trajectory_showcase' / name
        command = [sys.executable, '-B', 'src/build_trajectory_showcase.py',
                   '--source', case['source'], '--object', case['object_id'],
                   '--horizon', str(case['horizon_days']), '--output', str(output)]
        if case['source'] == 'confirmation':
            command += ['--method', case['method'], '--tolerance', str(case['tolerance_km'])]
        subprocess.run(command, cwd=root, check=True)
        check = subprocess.run([sys.executable, '-B', 'src/verify_trajectory_showcase.py', str(output)],
                               cwd=root, check=True, capture_output=True, text=True)
        verification = json.loads(check.stdout)
        if verification.get('passed') is not True:
            raise ValueError('coordinate verification did not pass')
        audit = json.loads((output / 'audit.json').read_text())
        scene_names = list(audit['scenes'])
        for scene in scene_names:
            subprocess.run([str(renderer), str(output / (scene + '.json')), str(output / scene)], cwd=root, check=True)
        subprocess.run([str(media_verifier)] + [str(output / scene) for scene in scene_names], cwd=root, check=True)
        entries.append(dict(case=case, audit_sha256=sha(output / 'audit.json'),
                            verification_sha256=sha(output / 'verification.json'),
                            scenes=verification['scenes']))
        print(json.dumps(dict(completed=name, scenes=len(scene_names))), flush=True)
    manifest = dict(schema_version=1, scope=config['selection_scope'],
                    manual_visual_review_required=True, entries=entries,
                    source_sha256={p:sha(root / p) for p in [
                        'configs/trajectory_showcase.json', 'src/run_trajectory_showcase.py',
                        'src/build_trajectory_showcase.py', 'src/verify_trajectory_showcase.py',
                        'src/render_trajectory_animation.swift', 'src/verify_animation_media.swift']},
                    renderer_binary_sha256=sha(renderer), media_verifier_binary_sha256=sha(media_verifier))
    (root / 'outputs/trajectory_showcase/build_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(cases=len(entries), unique_bodies=len({c['case']['object_id'] for c in entries}),
                          animations=sum(len(c['scenes']) for c in entries), visual_review_pending=True)))


if __name__ == '__main__':
    main()
