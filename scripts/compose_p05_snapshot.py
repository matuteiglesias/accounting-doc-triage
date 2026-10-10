#!/usr/bin/env python3
"""Compose private P05 evidence: migrated verified approvals + review-only candidates.

No approvals are inferred from matching scores. New approvals require an explicit,
content-checked review manifest; this operation never mutates canonical accounting.
"""
from __future__ import annotations
import argparse, hashlib, json, shutil
from collections import Counter, defaultdict
from pathlib import Path
import pandas as pd

SCHEMA = "acct.transaction-evidence@1"
KIND = {"application/pdf":".pdf","image/jpeg":".jpeg","image/png":".png"}
CORE = {"tx_id","source_row","transaction_id","ingest_ts","account_id"}


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for piece in iter(lambda:f.read(1048576),b""):
            h.update(piece)
    return h.hexdigest()


def verify_doc(root: Path, record: dict) -> Path:
    evidence_id = str(record["evidence_id"])
    assert len(evidence_id) == 64
    assert evidence_id == record["content_sha256"]
    assert record["media_type"] in KIND
    href = Path(record["href"])
    assert not href.is_absolute() and ".." not in href.parts and len(href.parts)==2 and href.parts[0]=="evidence"
    source = root / href
    assert source.is_file() and digest(source)==evidence_id, f"evidence hash invalid: {source}"
    return source


def identity_map(previous: pd.DataFrame, current: pd.DataFrame):
    excluded = CORE
    columns = [c for c in previous.columns if c in current.columns and c not in excluded]
    assert columns and {"Date","amount","Currency","payer","receiver","Flujo","Tipo","Box","status"}.issubset(columns)
    left=previous.copy(); right=current.copy()
    left["__key"]=left[columns].astype(str).agg("\x1f".join,axis=1)
    right["__key"]=right[columns].astype(str).agg("\x1f".join,axis=1)
    assert left.__key.is_unique and right.__key.is_unique, "ambiguous business identity: halt"
    merged=left.merge(right[["__key","tx_id","account_id"]],on="__key",how="left",validate="one_to_one",
                      suffixes=("_old","_new"))
    assert merged.tx_id_new.notna().all(), "not all previous transactions are preserved"
    out=dict(zip(merged.tx_id_old,merged.tx_id_new))
    assert len(out)==len(previous) and len(set(out.values()))==len(out)
    changes=merged[merged.account_id_old != merged.account_id_new]
    return out,changes[["tx_id_old","tx_id_new","account_id_old","account_id_new"]].to_dict("records")


def run(args):
    previous=Path(args.previous).resolve(strict=True)
    staged=Path(args.candidates).resolve(strict=True)
    ledger=Path(args.ledger).resolve(strict=True)
    review_file=Path(args.reviews).resolve(strict=True)
    target=Path(args.out).resolve()
    assert not target.exists(), f"refusing overwrite {target}"
    manifests=[json.loads((p/"manifest.json").read_text()) for p in (previous,staged)]
    oldmeta, metadata=manifests
    assert oldmeta["artifact"]==metadata["artifact"]==SCHEMA
    assert metadata.get("candidate_only") is True
    assert metadata["source_ledger_sha256"]==digest(ledger)
    old_tx=pd.read_csv(previous/"required_transactions.csv",dtype=str,keep_default_na=False)
    tx=pd.read_csv(staged/"required_transactions.csv",dtype=str,keep_default_na=False)
    assert len(tx)==metadata["required_transaction_count"] and tx.tx_id.is_unique
    expected=hashlib.sha256("\n".join(sorted(tx.tx_id)).encode()).hexdigest()
    assert expected==metadata["required_tx_ids_sha256"]
    assert len(tx)==214 and len(old_tx)==183, "reconfirm frozen population before running"
    map_ids,account_changes=identity_map(old_tx,tx)
    required=tx.set_index("tx_id")
    old_links=pd.read_csv(previous/"transaction_evidence.csv",dtype=str,keep_default_na=False)
    incoming=pd.read_csv(staged/"transaction_evidence.csv",dtype=str,keep_default_na=False)
    assert set(old_links.tx_id).issubset(map_ids)
    assert set(incoming.status).issubset({"candidate"}) and not (incoming.status=="approved").any()
    loaded={}
    sources={}
    for dir_ in [staged,previous]:
        for doc in pd.read_csv(dir_/"evidence_documents.csv",dtype=str,keep_default_na=False).to_dict("records"):
            evidence_id=doc["evidence_id"]
            src=verify_doc(dir_,doc)
            if evidence_id in loaded:
                assert loaded[evidence_id]["media_type"]==doc["media_type"]
                continue
            loaded[evidence_id]=doc
            sources[evidence_id]=src

    # Newly reviewed JPEG photographs have no pdftotext derivative; the operator
    # supplies source hash + observed date/amount and explicit ledger reference.
    review=json.loads(review_file.read_text())
    assert review["source_ledger_sha256"]==digest(ledger)
    assert review["candidate_snapshot_sha256"]==digest(staged/"manifest.json")
    entries=review["approvals"]
    assert len(entries)==len(set((r["evidence_id"],r["tx_id"]) for r in entries))
    for item in entries:
        src=Path(item["source_path"]).resolve(strict=True)
        assert digest(src)==item["evidence_id"]==item["expected_sha256"], "source changed since human review"
        eid=item["evidence_id"]
        assert item["tx_id"] in required.index
        row=required.loc[item["tx_id"]]
        assert str(row["Flujo"])=="Pagos" and str(row["payer"])=="PM", "proof of payment must describe outgoing PM payment, not contribution/mirror"
        assert str(row["Currency"])==str(item["currency"])
        assert str(row["Tipo"])==str(item["type"])
        assert str(row["issuer"])==str(item["issuer"]), "issuer mismatch"
        assert str(row["Lugar"])==str(item["place"])
        assert str(row["Date"])==str(item["ledger_date"])
        assert abs(float(row["amount"])-float(item["amount"]))<=10, "amount mismatch"
        assert item["paid_date"] and abs((pd.Timestamp(row["Date"])-pd.Timestamp(item["paid_date"])).days)<=3, "payment date discrepancy"
        assert len(item["review_reason"].strip())>=35
        if eid not in loaded:
            med={".pdf":"application/pdf",".jpeg":"image/jpeg",".jpg":"image/jpeg",".png":"image/png"}.get(src.suffix.lower())
            assert med in KIND
            loaded[eid]={"evidence_id":eid,"content_sha256":eid,"media_type":med,
                         "display_name":src.name,"href":"evidence/"+eid+KIND[med]}
            sources[eid]=src
        # Candidate PDF must have been emitted by producer and approved source must
        # be the same byte identity. Images can use explicit manual review.
        if loaded[eid]["media_type"]=="application/pdf":
            assert ((incoming.evidence_id==eid)&(incoming.tx_id==item["tx_id"])).any(), "PDF not a producer candidate"
        else:
            assert item.get("review_medium")=="manual_visual_original", "JPEG needs documented manual visual review"

    relationships={}
    for rec in incoming.to_dict("records"):
        key=(rec["tx_id"],rec["evidence_id"],rec["relation"])
        relationships[key]="candidate"
    for rec in old_links.to_dict("records"):
        key=(map_ids[rec["tx_id"]],rec["evidence_id"],rec["relation"])
        relationships[key]=rec["status"] if rec["status"]=="approved" else relationships.get(key,"candidate")
    for item in entries:
        kind=item["relation"]
        assert kind in ("payment_proof","transfer_proof")
        relationships[(item["tx_id"],item["evidence_id"],kind)]="approved"

    # Previously approved relations can be demoted only with an explicit private
    # forensic correction record. This is not an automatic matcher decision.
    corrections=json.loads(Path(args.corrections).resolve(strict=True).read_text()) if args.corrections else {"demotions":[]}
    assert corrections.get("source_ledger_sha256",digest(ledger))==digest(ledger)
    demoted=set()
    for entry in corrections["demotions"]:
        key=(entry["tx_id"],entry["evidence_id"],entry["relation"])
        assert relationships.get(key)=="approved", "demotion must refer to an existing approved relation"
        assert len(entry["reason"])>=70 and entry["ledger_account"] and entry["document_account"]
        assert entry["ledger_account"]!=entry["document_account"]
        assert entry["evidence_id"] in loaded and entry["tx_id"] in required.index
        assert str(required.loc[entry["tx_id"]]["account_id"])==entry["ledger_account"]
        relationships[key]="candidate"
        demoted.add(key)

    assert all(t in required.index and evid in loaded for t,evid,_ in relationships)
    relations=pd.DataFrame([{"tx_id":t,"evidence_id":eid,"relation":kind,"status":s}
                             for (t,eid,kind),s in sorted(relationships.items())])
    assert not relations.duplicated(["tx_id","evidence_id","relation"]).any()
    assert set(relations.status).issubset({"candidate","approved"})
    approved=relations[(relations.status=="approved")&relations.relation.isin(["payment_proof","transfer_proof"])]
    # Preserve prior approved links and ensure no loss during tx_id re-keying.
    prior={(map_ids[x.tx_id],x.evidence_id,x.relation) for x in old_links.itertuples() if x.status=="approved"}
    got={(x.tx_id,x.evidence_id,x.relation) for x in approved.itertuples()}
    assert prior-got==demoted and len(prior)==int((old_links.status=="approved").sum())
    assert len(set(approved.tx_id))>=30

    target.mkdir(parents=True)
    (target/"evidence").mkdir()
    for eid in sorted(loaded):
        dest=target/loaded[eid]["href"]
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(sources[eid],dest)
        assert digest(dest)==eid
    docs=pd.DataFrame([loaded[k] for k in sorted(loaded)])
    docs.to_csv(target/"evidence_documents.csv",index=False)
    relations.to_csv(target/"transaction_evidence.csv",index=False)
    tx.to_csv(target/"required_transactions.csv",index=False)
    reg=[]
    for row in tx.to_dict("records"):
        sub=relations[relations.tx_id==row["tx_id"]]
        a=int((sub.status=="approved").sum())
        c=int((sub.status=="candidate").sum())
        reg.append({"tx_id":row["tx_id"],"Date":row["Date"],"payer":row["payer"],
         "Tipo":row["Tipo"],"amount":row["amount"],"Currency":row["Currency"],
         "beneficiary_or_concept":" | ".join([str(row.get(c,"")) for c in ("receiver","Detalle","notes") if str(row.get(c,""))]),
         "approved_count":a,"candidate_count":c,
         "document_status":"approved" if a else ("candidate" if c else "missing")})
    reg=pd.DataFrame(reg)
    reg.to_csv(target/"evidence_register.csv",index=False)
    reg.loc[reg.document_status!="approved"].to_csv(target/"missing_transactions.csv",index=False)
    metadata.update({"run_id":args.run_id,"snapshot_id":target.parent.name,
     "approved_relations":int(len(approved)),"candidate_relations":int((relations.status=="candidate").sum()),
     "transactions_with_approved_proof":int(approved.tx_id.nunique()),
     "transactions_missing_approved_proof":int(len(tx)-approved.tx_id.nunique()),
     "accounting_coverage":{"numerator":int(approved.tx_id.nunique()),"denominator":len(tx),"pct":round(100*approved.tx_id.nunique()/len(tx),2)},
     "document_coverage":{"numerator":int(approved.evidence_id.nunique()),"denominator":len(docs),
        "pct":round(100*approved.evidence_id.nunique()/len(docs),2)},
      "legacy_approval_carryover":len(prior)-len(demoted),"explicit_demoted_relations":len(demoted),
     "operator_approved_new_relations":len(entries),
     "source_review_manifest_sha256":digest(review_file),
     "accounting_authority_changed":False,"new_approvals_operator_verified":True})
    (target/"manifest.json").write_text(json.dumps(metadata,indent=2,ensure_ascii=False)+"\n")
    audit={"prior_approved_relations":len(prior),
      "prior_approved_tx":int(old_links[old_links.status=="approved"].tx_id.nunique()),
      "current_approved_tx":int(approved.tx_id.nunique()),
      "candidate_relations":int((relations.status=="candidate").sum()),
      "new_approvals":len(entries),"demotions":corrections["demotions"],
      "rekeyed_tx_ids":sum(k!=v for k,v in map_ids.items()),
      "account_id_changes":account_changes,
      "review_manifest":str(review_file),"review_manifest_sha256":digest(review_file),
      "approved_by_year":tx[tx.tx_id.isin(approved.tx_id)].Date.str[:4].value_counts().sort_index().to_dict(),
      "eligible_by_year":tx.Date.str[:4].value_counts().sort_index().to_dict(),
      "parent_snapshot":str(previous),"candidate_snapshot":str(staged),
      "exact_ledger_sha256":digest(ledger)}
    (target/"P05_R2_audit.json").write_text(json.dumps(audit,indent=2,ensure_ascii=False)+"\n")
    qa=[
     ("ledger_sha256",digest(ledger)==metadata["source_ledger_sha256"]),
     ("frozen_tx_ids_hash",hashlib.sha256("\n".join(sorted(tx.tx_id)).encode()).hexdigest()==metadata["required_tx_ids_sha256"]),
     ("old_approved_preserved_except_reviewed_demotions",prior-got==demoted),
     ("tx_id_all_current",set(relations.tx_id).issubset(set(tx.tx_id))),
     ("media_original_sha256",all(digest(target/d["href"])==d["evidence_id"] for d in docs.to_dict("records"))),
     ("no_candidate_promotions_without_review",len(got-prior)==len(entries)),
     ("status_exclusive",not relations.duplicated(["tx_id","evidence_id","relation"]).any()),
     ("no_ledger_change",digest(ledger)==metadata["source_ledger_sha256"]),
    ]
    pd.DataFrame([{"check":k,"status":"pass" if v else "fail"} for k,v in qa]).to_csv(target/"evidence_coverage_qa.csv",index=False)
    assert all(x[1] for x in qa),qa
    print(json.dumps({"snapshot":str(target),"qa":"PASS","required":len(tx),
     "prior_approved_tx":audit["prior_approved_tx"],"approved_tx":audit["current_approved_tx"],
     "approved_relations":len(approved),"approved_new_relations":len(entries),"corrections":len(demoted),
     "candidates":audit["candidate_relations"],"rekeyed":audit["rekeyed_tx_ids"],
     "by_year":audit["approved_by_year"]},indent=2))


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    for opt in ("previous","candidates","ledger","reviews","out","run-id"):
        ap.add_argument("--"+opt,required=True)
    ap.add_argument("--corrections",required=False)
    run(ap.parse_args())
