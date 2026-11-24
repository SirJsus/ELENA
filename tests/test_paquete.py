"""Paso 0: el paquete se instala, importa y el comando responde."""

import pytest

import elena
from elena import cli


def test_version():
    assert elena.__version__ == "0.1.0"


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as salida:
        cli.main(["--version"])
    assert salida.value.code == 0
    assert "elena 0.1.0" in capsys.readouterr().out


def test_dependencias():
    import numpy  # noqa: F401
    import textual  # noqa: F401
    import tomli_w  # noqa: F401
