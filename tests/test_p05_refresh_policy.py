from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / file)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_p05_scope_includes_service_uppercase_without_crossing_family_boundary() -> None:
    c = _load("campaign_policy", "scripts/run_evidence_campaign.py")
    rows = [
        dict(tx_id="a", Date="2025-01-01", amount="120", Currency="ARS", Box="Property Management",
             payer="PM", Tipo="Servicio", status="pagado"),
        dict(tx_id="b", Date="2025-01-01", amount="120", Currency="ARS", Box="Family Business",
             payer="PM", Tipo="Servicio", status="pagado"),
        dict(tx_id="c", Date="2025-01-01", amount="120", Currency="ARS", Box="Property Management",
             payer="Inq", Tipo="Servicio", status="pagado"),
        dict(tx_id="d", Date="2025-01-01", amount="120", Currency="ARS", Box="Property Management",
             payer="PM", Tipo="Servicio", status="pendiente"),
    ]
    eligible, _ = c.select_required(pd.DataFrame(rows))
    assert eligible.tx_id.tolist() == ["a"]


def test_evidence_rekey_requires_unique_full_semantic_identity() -> None:
    m = _load("composition_policy", "scripts/compose_p05_snapshot.py")
    rows = [
        dict(tx_id="old1", Date="2024-05-01", amount="320.0", Currency="ARS",
             payer="PM", receiver="Impuestos", Flujo="Pagos", Tipo="Impuestos",
             Box="Property Management", status="pagado",
             source_row="100", transaction_id="100", ingest_ts="2026-09-01",
             account_id=""),
        dict(tx_id="old2", Date="2024-06-01", amount="250.0", Currency="ARS",
             payer="MI", receiver="PM", Flujo="Contribucion", Tipo="Impuestos",
             Box="Property Management", status="pagado",
             source_row="101", transaction_id="101", ingest_ts="2026-09-01",
             account_id="AA"),
    ]
    left = pd.DataFrame(rows)
    right = left.copy()
    right["tx_id"] = ["new1", "new2"]
    right["source_row"] = ["108", "109"]
    right["transaction_id"] = ["108", "109"]
    right["ingest_ts"] = ["2026-10-10", "2026-10-10"]
    right.loc[0, "account_id"] = "NEW_ACCOUNT"
    txmap, changes = m.identity_map(left, right)
    assert txmap == {"old1":"new1","old2":"new2"}
    assert changes == [{"tx_id_old":"old1","tx_id_new":"new1",
                        "account_id_old":"","account_id_new":"NEW_ACCOUNT"}]


def test_evidence_rekey_fails_on_ambiguous_business_rows() -> None:
    m = _load("composition_duplicate_policy", "scripts/compose_p05_snapshot.py")
    row = dict(tx_id="a", Date="2024-01-01", amount="100", Currency="ARS",
               payer="PM", receiver="Impuestos", Flujo="Pagos",
               Tipo="Impuestos", Box="Property Management", status="pagado")
    old = pd.DataFrame([row, {**row, "tx_id":"b"}])
    new = pd.DataFrame([{**row,"tx_id":"x"}, {**row,"tx_id":"y"}])
    with pytest.raises(AssertionError, match="ambiguous business identity"):
        m.identity_map(old,new)
