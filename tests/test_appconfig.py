import pytest

from lib.appconfig import AppConfig, load_app_config, url_is_blocked


def test_missing_file_returns_empty_config(tmp_path):
    cfg = load_app_config(tmp_path / "nope.yaml")
    assert cfg == AppConfig(blocked_domains=frozenset())


def test_load_normalizes_domains(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "blocked_domains:\n"
        "  - Fool.com\n"
        "  - www.biztoc.com\n"
        "  - naturalnews.com\n"
    )
    cfg = load_app_config(p)
    assert cfg.blocked_domains == frozenset(
        {"fool.com", "biztoc.com", "naturalnews.com"}
    )


def test_empty_or_absent_key_is_empty(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("blocked_domains:\n")
    assert load_app_config(p).blocked_domains == frozenset()
    p.write_text("# nothing here\n")
    assert load_app_config(p).blocked_domains == frozenset()


def test_malformed_blocked_domains_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("blocked_domains: not-a-list\n")
    with pytest.raises(ValueError, match="blocked_domains"):
        load_app_config(p)


def test_url_is_blocked_exact_and_subdomain():
    blocked = frozenset({"fool.com", "investorshub.advfn.com"})
    assert url_is_blocked("https://www.fool.com/x/y", blocked)
    assert url_is_blocked("https://m.fool.com/article", blocked)
    assert url_is_blocked("https://investorshub.advfn.com/z", blocked)
    assert not url_is_blocked("https://advfn.com/other", blocked)
    assert not url_is_blocked("https://notfool.com/a", blocked)
    assert not url_is_blocked("", blocked)
    assert not url_is_blocked("https://anything.com/a", frozenset())
