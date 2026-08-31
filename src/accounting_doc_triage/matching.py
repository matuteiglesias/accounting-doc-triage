from __future__ import annotations

"""Conservative candidate matching between document observations and ledger rows."""

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd

from accounting_doc_triage.interpretation.model import AccountingDocumentObservation


@dataclass(frozen=True, slots=True)
class MatchConfig:
    date_window_days: int = 3
    amount_tolerance: float = 0.01

    def __post_init__(self) -> None:
        if self.date_window_days < 0:
            raise ValueError("date_window_days must be non-negative")
        if self.amount_tolerance < 0:
            raise ValueError("amount_tolerance must be non-negative")


_REQUIRED_LEDGER_COLUMNS = ("tx_id", "Date", "amount", "Currency")
_PAYMENT_KINDS = frozenset(
    {"payment_proof", "tax_payment_proof", "utility_payment_proof", "transfer_proof"}
)


def _relation_for(observation: AccountingDocumentObservation) -> str | None:
    if observation.document_kind == "transfer_proof":
        return "transfer_proof"
    if observation.document_kind in _PAYMENT_KINDS:
        return "payment_proof"
    return None


def _require_columns(ledger: pd.DataFrame) -> None:
    missing = [column for column in _REQUIRED_LEDGER_COLUMNS if column not in ledger.columns]
    if missing:
        raise ValueError(f"ledger candidate frame missing required columns: {missing}")


def candidate_matches(
    observation: AccountingDocumentObservation,
    ledger: pd.DataFrame,
    *,
    config: MatchConfig | None = None,
) -> pd.DataFrame:
    """Return review candidates; never approve or mutate ledger rows.

    First-generation matching intentionally requires a payment-like observation,
    exact currency, near-exact absolute amount, and payment date within a small
    bounded window. Ambiguity is preserved rather than resolved heuristically.
    """

    config = config or MatchConfig()
    _require_columns(ledger)
    columns = (
        "evidence_id",
        "candidate_tx_id",
        "relation",
        "match_status",
        "amount_delta",
        "date_delta_days",
        "match_reasons",
    )
    relation = _relation_for(observation)
    if (
        relation is None
        or observation.amount is None
        or observation.payment_date is None
        or observation.currency is None
    ):
        return pd.DataFrame(columns=columns)

    payment_date = pd.Timestamp(observation.payment_date).date()
    rows: list[dict] = []
    for _, row in ledger.iterrows():
        tx_id = str(row["tx_id"]).strip()
        if not tx_id:
            continue
        currency = str(row["Currency"]).strip().upper()
        if currency != observation.currency.upper():
            continue
        try:
            ledger_amount = float(row["amount"])
        except (TypeError, ValueError):
            continue
        amount_delta = abs(abs(ledger_amount) - abs(float(observation.amount)))
        if amount_delta > config.amount_tolerance:
            continue
        try:
            ledger_date = pd.Timestamp(row["Date"]).date()
        except (TypeError, ValueError):
            continue
        date_delta = abs((ledger_date - payment_date).days)
        if date_delta > config.date_window_days:
            continue
        reasons = ["currency_exact", "amount_exact"]
        reasons.append("date_exact" if date_delta == 0 else "date_within_window")
        rows.append(
            {
                "evidence_id": observation.evidence_id,
                "candidate_tx_id": tx_id,
                "relation": relation,
                "match_status": "candidate",
                "amount_delta": round(amount_delta, 2),
                "date_delta_days": date_delta,
                "match_reasons": ";".join(reasons),
            }
        )

    if len(rows) == 1:
        rows[0]["match_status"] = "unique_candidate"
    elif len(rows) > 1:
        for row in rows:
            row["match_status"] = "ambiguous_candidate"
    return pd.DataFrame(rows, columns=columns)


def candidate_matches_many(
    observations: Iterable[AccountingDocumentObservation],
    ledger: pd.DataFrame,
    *,
    config: MatchConfig | None = None,
) -> pd.DataFrame:
    frames = [candidate_matches(obs, ledger, config=config) for obs in observations]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return candidate_matches(
            AccountingDocumentObservation(
                evidence_id="",
                conversion_id="",
                document_kind="other",
                issuer=None,
                currency=None,
                amount=None,
                document_date=None,
                payment_date=None,
                due_date=None,
                external_reference=None,
                account_reference=None,
                period=None,
                parser_confidence_grade=None,
                review_required=True,
            ),
            ledger.iloc[0:0],
            config=config,
        )
    return pd.concat(frames, ignore_index=True)
