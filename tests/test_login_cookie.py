from __future__ import annotations

import sqlite3

from jobbot.cli import _login_cookie_present


def _make_cookie_db(path, names):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
    conn.executemany(
        "INSERT INTO cookies (host_key, name) VALUES (?, ?)",
        [(".zhipin.com", name) for name in names],
    )
    conn.commit()
    conn.close()


def test_login_cookie_missing(tmp_path):
    assert _login_cookie_present(tmp_path) is False


def test_tracking_cookies_are_not_login(tmp_path):
    _make_cookie_db(
        tmp_path / "Default" / "Cookies",
        ["HMACCOUNT", "Hm_lvt_x", "lastCity"],
    )
    assert _login_cookie_present(tmp_path) is False


def test_zp_token_is_login(tmp_path):
    _make_cookie_db(tmp_path / "Default" / "Cookies", ["HMACCOUNT", "zp_token"])
    assert _login_cookie_present(tmp_path) is True


def test_zp_at_is_login(tmp_path):
    _make_cookie_db(tmp_path / "Default" / "Cookies", ["HMACCOUNT", "zp_at"])
    assert _login_cookie_present(tmp_path) is True


def test_geek_token_in_network_path(tmp_path):
    _make_cookie_db(
        tmp_path / "Default" / "Network" / "Cookies",
        ["geek_zp_token"],
    )
    assert _login_cookie_present(tmp_path) is True
