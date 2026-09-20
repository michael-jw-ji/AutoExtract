# Runnable demo documents

Each `.txt` is a real document from the live corpus. Each has a sibling
`.gold.json` — the constructed ground truth it was rendered from, which is how
`demo_case.py` can say "correct" rather than just "parsed".

```powershell
python scripts\demo_case.py examples\invoice_missing_totals.txt --raw   # enrichment OFF
python scripts\demo_case.py examples\invoice_missing_totals.txt         # enrichment ON
```

| File | Messiness | Shows |
|---|---|---|
| `invoice_missing_totals.txt` | totals not printed | **the best one.** Invented `VND-00001`, 5 wrong category codes. Enriched it becomes VALID — and is *still* wrong on `tax`/`total`, which is the "valid ≠ correct" point |
| `invoice_currency_symbols.txt` | mixed currency symbols | invented `VND-94121`, plus two category codes that don't even match the code *format* |
| `invoice_scanned_fax.txt` | OCR artefacts | 5 line items, noisier read |

Email examples land here once `.\scripts\rebuild_email.ps1` finishes.
