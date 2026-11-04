"""Check GUI job completion and cleanup without requiring GPU hardware."""
import json
from types import SimpleNamespace
import pytest
from lookandpick.sam_prompt import Studio


@pytest.fixture(autouse=True)
def isolated_fake_gpu_lease(tmp_path, monkeypatch):
    # These jobs mock inference; they must not wait for or mutate a live SAM worker's lease.
    monkeypatch.setattr('lookandpick.gpu.LOCK', tmp_path / '.gpu.lock')


def test_job_saves_inference_outputs(tmp_path, monkeypatch):
    studio = Studio(tmp_path, '/fake/python')
    job = 'clip'
    folder = tmp_path / job
    folder.mkdir()
    studio.busy = True
    def run(command, **kwargs):
        assert command[0] == '/fake/python'
        assert command[command.index('--prompt') + 1] == 'mug'
        frames = folder / 'frames'
        frames.mkdir()
        (frames / 'metadata.json').write_text(json.dumps({'fps': 10, 'count': 2}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('lookandpick.sam_prompt.subprocess.run', run)
    studio.process(job, 'mug')
    assert studio.jobs[job] == {'state': 'ready', 'fps': 10, 'count': 2}
    assert not studio.busy


def test_failed_job_releases_worker(tmp_path, monkeypatch):
    studio = Studio(tmp_path, '/fake/python')
    (tmp_path / 'clip').mkdir()
    studio.busy = True
    def run(*args, **kwargs):
        kwargs['stdout'].write('No CUDA GPU available')
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr('lookandpick.sam_prompt.subprocess.run', run)
    studio.process('clip', 'mug')
    assert studio.jobs['clip']['state'] == 'error'
    assert 'No CUDA' in studio.jobs['clip']['message']
    assert not studio.busy


def test_rejects_invalid_prompt_and_busy_worker(tmp_path):
    studio = Studio(tmp_path, '/fake/python')
    with pytest.raises(ValueError, match='prompt'):
        studio.submit(b'clip', ' ')
    studio.busy = True
    with pytest.raises(ValueError, match='processing'):
        studio.submit(b'clip', 'mug')
