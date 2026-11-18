"""Pruebas de funciones puras añadidas en la revisión de mejoras."""
import pytest

from media_store import RefIndex, is_referenced
from services.commerce import valid_transition

BASE = "https://res.cloudinary.com/demo/image/upload"


def test_ref_index_matches_transformed_and_versioned_urls():
    asset = f"{BASE}/v1/valerion_catalogo/abc.webp"
    refs = {f"{BASE}/f_auto,w_640/v9/valerion_catalogo/abc", "/static/img/x.svg"}
    assert is_referenced(asset, refs)
    assert is_referenced(asset, RefIndex(refs))
    assert not is_referenced(f"{BASE}/v1/valerion_catalogo/otra.webp", RefIndex(refs))


@pytest.mark.parametrize(
    "before,after,ok",
    [
        ("pendiente", "pagada", True),
        ("pagada", "en_preparacion", True),
        ("pagada", "cancelada", True),
        ("enviada", "entregada", True),
        ("entregada", "cancelada", False),
        ("cancelada", "pagada", False),
        ("enviada", "pagada", False),
    ],
)
def test_order_transitions(before, after, ok):
    assert valid_transition(before, after) is ok
