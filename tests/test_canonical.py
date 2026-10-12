"""Store round-trip test."""
import os
import tempfile

from store.canonical import CanonicalStore
from store.schemas import Bar, Provenance


def test_append_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        s = CanonicalStore(root=tmp)
        s.append("bars", Bar(symbol="NVDA", date="2026-10-09", open=1.0, high=2.0,
                             low=0.5, close=1.5, volume=100,
                             provenance=Provenance(source="test")))
        recs = s.read("bars", where={"symbol": "NVDA"})
        assert len(recs) == 1
        assert recs[0]["close"] == 1.5
        assert recs[0]["provenance"]["source"] == "test"
