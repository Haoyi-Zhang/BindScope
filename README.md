# BindScope

Context-safe prepared-statement reuse under an explicit fresh-prepare contract.
The implementation includes a finite catalog model, a SQLite C-API adapter,
typed result comparisons, read-observation diagnostics, and a local connection
pool. PostgreSQL is an optional, unvalidated pilot rather than an evaluated
implementation.

## Tests and retained results

Use Python 3.11 or newer and SQLite 3.35 or newer. Linux uses a system SQLite
library; Windows can use the SQLite DLL supplied with Python. No database
server is required for the tested SQLite paths.

```sh
python -m unittest discover -s tests -v
python experiments/verify_results.py
```

The current suite contains 70 tests. Retained measurements include 864,000 model
decisions, 140,000 paired native uses, 300 read-observation pairs, and 260,000
leased executions. These are the recorded study, not new measurements on every
machine running CI. The timing comparison includes cases where BindScope is
slower; inspect the complete tables rather than an isolated speedup.

For new local measurements, use a separate working copy:

```sh
python experiments/reproduce.py --timings
```

The result verifier compares typed schemas and row multisets including
multiplicity. SQL results without an ORDER BY contract are not compared by
incidental row order. Evaluated sources and third-party notices are retained in
`provenance/` and `licenses/`. Original code uses the included MIT license.
