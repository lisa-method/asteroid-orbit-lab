"""Repeat the byte/provenance audit on the propagation runtime without overwriting its first run."""
import json
from pathlib import Path
import platform
import audit_selector_raw_v3 as audit
from prepare_selector_holdout24 import check_hashes

EXPECTED='af6dd0c4ba72e08c3be5c5bbe3507051e0c387157f35867c7d7eb0e6e028ad13'


def verify(root):
    if platform.python_version() != '3.14.7':
        raise ValueError('Use the pinned Python 3.14.7 runtime')
    method=root/'outputs/selector_v3/method_freeze.json'
    if audit.sha(method)!=EXPECTED:raise ValueError('Unexpected frozen method')
    frozen=json.loads(method.read_text());check_hashes(root,frozen['hashes'])
    previous=json.loads((root/audit.OUTPUT).read_text())
    audit.OUTPUT='outputs/selector_holdout24/raw_verification_python314.json'
    result=audit.audit(root)
    if {k:v for k,v in result.items() if k!='runtime'}!={k:v for k,v in previous.items() if k!='runtime'}:
        raise ValueError('Raw audit findings differ across runtimes')
    check_hashes(root,frozen['hashes'])
    if audit.sha(method)!=EXPECTED:raise ValueError('Method changed during audit')
    audit.immutable_json(root/'outputs/selector_holdout24/raw_runtime_validation.json',{
        'passed':True,'findings_identical':True,'method_freeze_sha256':EXPECTED,
        'first_audit_python':previous['runtime']['python_version'],'repeated_audit_python':result['runtime']['python_version'],
        'repeated_audit_sha256':audit.sha(root/audit.OUTPUT),'wrapper_sha256':audit.sha(Path(__file__))})
    print(json.dumps({'passed':True,'raw_paths':result['inventory']['unique_raw_paths'],'runtime':'3.14.7'}))

if __name__=='__main__':verify(Path('.').resolve())
