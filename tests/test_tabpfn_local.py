from slothwatch.tabpfn_local import enabled_checkpoint, local_checkpoint


def test_tabpfn_never_activates_without_explicit_opt_in(monkeypatch, tmp_path):
    checkpoint = tmp_path / "tabpfn-3.5.safetensors"
    checkpoint.write_bytes(b"test-placeholder-not-a-real-model")
    monkeypatch.setenv("TABPFN_MODEL_PATH", str(checkpoint))
    monkeypatch.delenv("SLOTHWATCH_ENABLE_TABPFN", raising=False)
    assert local_checkpoint() == checkpoint
    assert enabled_checkpoint() is None
    monkeypatch.setenv("SLOTHWATCH_ENABLE_TABPFN", "1")
    assert enabled_checkpoint() == checkpoint


def test_role_specific_tabpfn_checkpoint_takes_precedence(monkeypatch, tmp_path):
    regressor = tmp_path / "regressor.safetensors"
    classifier = tmp_path / "classifier.safetensors"
    regressor.write_bytes(b"regressor placeholder")
    classifier.write_bytes(b"classifier placeholder")
    monkeypatch.setenv("SLOTHWATCH_ENABLE_TABPFN", "true")
    monkeypatch.setenv("TABPFN_MODEL_PATH", str(regressor))
    monkeypatch.setenv("TABPFN_CLASSIFIER_MODEL_PATH", str(classifier))
    assert enabled_checkpoint("classifier") == classifier
    assert enabled_checkpoint("regressor") == regressor
