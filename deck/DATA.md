# How the test data was made

**All benchmark data is synthetic. We wrote it ourselves, with a script. It isn't real-world data, and results on it aren't real-world performance.**

The generator is `evals/generators/cases.py`. It writes `evals/cases/dev.jsonl`, `validation.jsonl` and `heldout.jsonl`, and a `MANIFEST.json` with each file's SHA-256 hash. Every case has `"synthetic": true`.

## No external dataset

No external dataset is used, so there is no dataset licence to check. The generator only uses Python's standard library, and its only inputs are the lists and templates in its own code. The organisers' kit isn't used.

No real records are used. Supplier names, addresses, invoice numbers, amounts and account details are all invented. IBANs use real bank codes (`BUKB`, `NWBK`, `BARC`, `LOYD`) with random digits, and they aren't built with valid check digits. We didn't check the invented supplier names against any company register, so a match with a real business would be accidental.

The demo company in `src/payoai/fixtures.py` is separate from the benchmark. It is also fictional, but it uses the name of a real company (Figma) for a fictional supplier, with invented details. This should be changed or labelled before the demo.

## How a case is built

1. A seeded random generator makes a clean UK invoice: supplier, addresses, invoice number, dates, one line item, a net amount of £200 to £900 and VAT at 20%. The customer is always Northwind Studio Ltd, which is fictional.
2. A scenario function edits that clean invoice and states the correct result. For example, `changed_iban` replaces the bank details and adds a note saying they changed, and it expects `BLOCK` with the rule `DESTINATION_CHANGED`.
3. The case also sets up the world around the invoice: the Verified Destination, usual amount range, balance, reserve, any earlier bill and any provider fault.
4. `evals/layouts.py` renders the facts as plain text in one of five layouts when the case runs.

Dates are fixed around 8 November 2026. The seeds are 101 for development, 202 for validation and 303 for held-out. We rebuilt all three splits in memory, and they match the committed hashes exactly.

## Where expected results come from

Expected results come from each scenario's definition, not from running PayoAI. This stops a bug in PayoAI from defining its own right answer.

They aren't independent, though. **The same team wrote the generator and the agent, and the scenarios name the agent's own rule IDs**, such as `UK-ARITH-TOTAL`. Each scenario tests a failure we already thought of and built a rule for. This is the main weakness of this benchmark, and a good result here shows that the code does what we meant it to do. It doesn't show that we thought of the right failures.

## Splits

| Split | Cases | Suppliers | Layouts | Seed |
|---|---|---|---|---|
| Development | 90 | 6 | `labelled`, `table`, `email` | 101 |
| Validation | 20 | 3 | `labelled`, `email` | 202 |
| Held-out | 40 | 6 | `compact`, `letter` | 303 |

| Category | Development | Validation | Held-out |
|---|---|---|---|
| Legitimate | 30 | 10 | 5 |
| Invalid invoice | 14 | 6 | 6 |
| Fraud | 16 | 4 | 11 |
| Duplicate | 6 | 0 | 4 |
| Cash and amount | 6 | 0 | 3 |
| Provider faults | 18 | 0 | 11 |

Held-out has 31 bank cases and 9 crypto cases. Three of its cases have text aimed at an AI injected into the invoice.

Three things about these splits limit what can be claimed:

- **Validation is narrow.** It takes the first 20 scenarios from a fixed list, so it has no duplicate, cash or fault cases.
- **Legitimate held-out cases are few.** There are only 5, so any false-block count rests on 5 cases.
- **One held-out scenario appears twice.** The list of 39 scenarios wraps round, so `poisoned_with_injection` is used twice.

## What makes held-out different

| Different from development | Details |
|---|---|
| Supplier names | Six names that development never uses: Kestrel Analytics, Fernhill Catering, Ironbridge Electrical, Saffron Translation, Glasshouse Recruitment and Pennine Freight |
| Invoice layouts | `compact` and `letter`, which development never uses |
| Five scenarios | `poisoned_with_injection`, `crypto_timeout_after_accept`, `crypto_pending_then_failed`, `changed_iban_and_bad_vat` and `duplicate_with_changed_details`, 6 cases in all |
| Random seed | Different amounts, dates, invoice numbers and account details |

| The same as development | Why it matters |
|---|---|
| The generator and its authors | Held-out is unseen by the system, but not by the people who built it |
| The other 34 of its 39 scenarios | It tests the same rules, on new surface details |
| The invoice structure, customer and line-item wording | The variety is much narrower than real invoices |
| Plain text only | No PDFs, scans or images |

The cases and their expected results sit in the repository, so "held-out" relies on process. The runner needs an explicit unlock phrase for this split, it refuses a file whose hash differs from the manifest, and each run is logged in `eval_runs`.

These are the hashes at commit `d94d88b`. `evals/cases/MANIFEST.json` is the authority.

| File | SHA-256 |
|---|---|
| `dev.jsonl` | `a4ea6fb78071e7c26cf66ff85c26e6cc15ad23b97adbf71279ca9f396377bbf7` |
| `validation.jsonl` | `b3254579c733a5424d9b3e85d3c94a99cdb5233b70328c9f1c62d58690100958` |
| `heldout.jsonl` | `2ba6dd463a721fdc93e17a9439f7e9079bc7b3c4ec2aabf4689d3b7f649850fe` |

## How the layouts affect each suite

The unseen layouts only test something where a model or template actually reads the text.

- **`full_agent`** sends the rendered text to the real model, so unseen layouts are a real test of extraction.
- **`routines` and `faults`** replace the model with a fake that returns the correct fields. Layouts and names barely matter there, and those suites test the checks and the money path.
- **The rules-only baseline** has regular expressions for the `labelled` layout only. Held-out doesn't use that layout, so this baseline is expected to do badly there by construction.

## Regenerating

The script is deterministic, and re-running it overwrites the case files and manifest with the same content on Python 3.12.

```bash
uv run python evals/generators/cases.py
```

See `docs/evaluation.md` for how the cases are run and judged.
