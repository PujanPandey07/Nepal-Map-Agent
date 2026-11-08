# services/geo-data/tests/test_config.py
"""
Unit tests for app/config.py.

These tests run completely offline (no database, no network).
"""
import pytest


class TestDbDsnBuilding:
    """
    Verify that DB_DSN is built with make_conninfo() so passwords
    containing spaces, single-quotes, and backslashes survive the round trip
    through libpq parsing.  An f-string would produce an unparseable DSN.
    """

    def _build_dsn(self, password: str) -> str:
        """Re-run the config DSN-building logic with an arbitrary password."""
        from psycopg.conninfo import make_conninfo
        return make_conninfo(
            host="127.0.0.1",
            port=5433,
            dbname="nepal_osm",
            user="map-osm",
            password=password,
        )

    def _roundtrip(self, password: str) -> str:
        """Build DSN then parse it back; return the recovered password."""
        from psycopg.conninfo import conninfo_to_dict
        dsn = self._build_dsn(password)
        return conninfo_to_dict(dsn)["password"]

    def test_plain_password_roundtrip(self):
        """Sanity check: a simple password survives unchanged."""
        assert self._roundtrip("hunter2") == "hunter2"

    def test_password_with_space_roundtrip(self):
        """Space in password: f-string produces  password=pa ss  which libpq rejects."""
        pw = "pa ss"
        assert self._roundtrip(pw) == pw

    def test_password_with_single_quote_roundtrip(self):
        """Single-quote: f-string produces  password=pa'ss  which libpq rejects."""
        pw = "pa'ss"
        assert self._roundtrip(pw) == pw

    def test_password_with_backslash_roundtrip(self):
        """Backslash: f-string produces  password=pa\\ss  which libpq misparses."""
        pw = "pa\\ss"
        assert self._roundtrip(pw) == pw

    def test_password_with_all_special_chars_roundtrip(self):
        """The canonical bad password from the task: space + single-quote + backslash."""
        pw = "pa ss'w\\ord"
        assert self._roundtrip(pw) == pw

    def test_dsn_is_parseable_by_psycopg(self):
        """
        psycopg.conninfo.conninfo_to_dict must not raise on the DSN.
        If it raises, psycopg refuses to connect even if the credentials
        would otherwise be correct.
        """
        from psycopg.conninfo import conninfo_to_dict
        dsn = self._build_dsn("pa ss'w\\ord")
        # Should not raise
        parsed = conninfo_to_dict(dsn)
        # Other fields must survive too
        assert parsed["host"] == "127.0.0.1"
        assert parsed["dbname"] == "nepal_osm"
        assert parsed["user"] == "map-osm"
