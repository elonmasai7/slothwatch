from pathlib import Path


def test_make_installs_python_packages_only_inside_project_venv():
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "Makefile").read_text()
    assert '$(SYSTEM_PYTHON) -m venv "$(VENV)"' in makefile
    assert '$(VENV_PYTHON) -m pip install -e ".[integrations]"' in makefile
    assert '$(VENV_PYTHON) -m pip install -e ".[all]"' in makefile
    assert "python3 -m pip install -e" not in makefile
    assert "--break-system-packages" not in makefile


def test_ollama_has_explicit_install_and_model_pull_targets():
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "Makefile").read_text()
    installer = (root / "scripts" / "install_ollama.sh").read_text()
    setup = (root / "scripts" / "setup_ollama.sh").read_text()
    assert "install-ollama:" in makefile
    assert "setup-ollama:" in makefile
    assert "ollama.com/install.sh" in installer
    assert 'ollama pull "$MODEL"' in setup
