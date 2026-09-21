import hashlib
import json
from pathlib import Path

import pytest

from scripts.check_249_hotfix import compare, validate_manifest


def fixture(root):
    relative = 'src/javert/synthetic.py'
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'SYNTHETIC_CODE')
    return {'version': 1, 'base_commit': 'a' * 40, 'hotfix_paths': [relative],
            'files': {relative: hashlib.sha256(path.read_bytes()).hexdigest()}}


def test_match_changed_missing_and_symlink(tmp_path):
    manifest = fixture(tmp_path)
    assert compare(tmp_path, manifest)['hotfix_match']
    path = tmp_path / next(iter(manifest['files']))
    path.write_bytes(b'CHANGED_CODE')
    assert compare(tmp_path, manifest)['differences'][0]['status'] == 'DIFFERENT'
    path.unlink()
    assert compare(tmp_path, manifest)['differences'][0]['status'] == 'MISSING'
    other = tmp_path / 'secret.env'
    other.write_text('SYNTHETIC_SECRET_DO_NOT_READ')
    path.symlink_to(other)
    result = compare(tmp_path, manifest)
    assert result['differences'][0]['status'] == 'SYMLINK'
    assert 'SYNTHETIC_SECRET' not in json.dumps(result)


@pytest.mark.parametrize('path', ['../.env', '/etc/passwd', 'configs/llm.yaml', 'src/../.env', 'output/data.json'])
def test_unsafe_manifest_rejected(path):
    with pytest.raises(ValueError):
        validate_manifest({'version': 1, 'base_commit': 'a'*40, 'files': {path: 'a'*64}, 'hotfix_paths': []})


def test_recovered_release_matches_frozen_delivery():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / 'deploy/249/known-release.json').read_text())
    result = compare(root, manifest)
    assert result['disk_match'], result['differences']
    assert result['hotfix_files'] == 14
