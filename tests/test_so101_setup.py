"""The SO-101 setup script refuses unsafe contexts; it never touches hardware in these tests."""
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("so101_setup", Path(__file__).parent.parent / "scripts" / "so101_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_refuses_without_a_terminal():
    with pytest.raises(SystemExit, match="interactive"):
        setup.require_present(stdin=io.StringIO(), stdout=Tty(), environ={})


def test_refuses_over_ssh():
    with pytest.raises(SystemExit, match="SSH"):
        setup.require_present(stdin=Tty(), stdout=Tty(), environ={"SSH_CONNECTION": "1 2 3 4"})


def test_refuses_wrong_confirmation_and_accepts_the_exact_one():
    with pytest.raises(SystemExit, match="Confirmation"):
        setup.require_present(stdin=Tty(), stdout=Tty(), environ={}, ask=lambda _: "yes")
    setup.require_present(stdin=Tty(), stdout=Tty(), environ={}, ask=lambda _: setup.CONFIRM)


def test_missing_port_is_refused_before_anything_else(monkeypatch):
    monkeypatch.setattr(setup, "find_ports", lambda: [])
    with pytest.raises(SystemExit, match="does not exist"):
        setup.main(["setup-motors", "--port", "/dev/ttyACM0"])


def test_find_ports_lists_without_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(setup, "find_ports", lambda: ["/dev/ttyACM0"])
    setup.main(["find-ports"])
    assert "/dev/ttyACM0" in capsys.readouterr().out
