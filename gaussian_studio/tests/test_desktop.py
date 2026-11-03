import json
from pathlib import Path
import threading
from unittest.mock import Mock
import urllib.request
import pytest
from gaussian_studio.desktop import DesktopBackend
from gaussian_studio.install_desktop import desktop_entry,exec_quote
from gaussian_studio import start


def test_default_launcher_opens_desktop_and_browser_mode_is_explicit(monkeypatch,tmp_path):
    desktop=Mock();browser=Mock()
    monkeypatch.setattr(start,'launch_desktop',desktop);monkeypatch.setattr(start,'launch_browser_service',browser)
    monkeypatch.setattr('sys.argv',['start.py','--data',str(tmp_path)])
    start.main();desktop.assert_called_once_with(tmp_path,0);browser.assert_not_called()
    monkeypatch.setattr('sys.argv',['start.py','--browser','--data',str(tmp_path)])
    start.main();browser.assert_called_once_with(tmp_path,8791)


def test_application_menu_entry_handles_checkout_paths_with_spaces(tmp_path):
    root=tmp_path/'My Studio'
    entry=desktop_entry(root,python='/usr/bin/python3')
    assert f'Exec="/usr/bin/python3" "{root}/start.py"' in entry
    assert 'Terminal=false' in entry and 'Type=Application' in entry
    assert str(root/'assets/splat-studio.svg') in entry
    assert exec_quote('a% "quoted" $value')=='"a%% \\"quoted\\" \\$value"'


def test_owned_backend_uses_ephemeral_port_and_stops_cleanly(tmp_path):
    backend=DesktopBackend(tmp_path/'captures')
    try:
        url=backend.start(timeout=15)
        assert url.startswith('http://127.0.0.1:')
        with urllib.request.urlopen(url+'/api/health') as response:assert json.load(response)['app']=='splat-studio'
        with urllib.request.urlopen(url+'/api/captures') as response:assert json.load(response)==[]
        assert backend.process.poll() is None
        temporary=Path(backend.directory.name)
    finally:backend.stop()
    assert backend.process.poll() is not None and not temporary.exists()
    backend.stop()


def test_cancelled_start_never_spawns_a_backend(tmp_path,monkeypatch):
    cancelled=threading.Event();cancelled.set();spawn=Mock()
    monkeypatch.setattr('gaussian_studio.desktop.subprocess.Popen',spawn)
    backend=DesktopBackend(tmp_path)
    assert backend.start(cancelled) is None
    spawn.assert_not_called();assert backend.directory is None


def test_failed_backend_start_cleans_up_its_readiness_directory(tmp_path,monkeypatch):
    process=Mock();process.poll.return_value=1
    monkeypatch.setattr('gaussian_studio.desktop.subprocess.Popen',Mock(return_value=process))
    backend=DesktopBackend(tmp_path)
    with pytest.raises(RuntimeError,match='exited'):backend.start()
    assert backend.directory is None


def test_missing_executable_is_a_clear_error_and_cleans_up(tmp_path,monkeypatch):
    monkeypatch.setattr('gaussian_studio.desktop.subprocess.Popen',Mock(side_effect=OSError('runtime unavailable')))
    backend=DesktopBackend(tmp_path)
    with pytest.raises(RuntimeError,match='could not be started'):backend.start()
    assert backend.directory is None
