# Third-party and data notices

No third-party Python source, server executable, client library binary, font
file, private data, or scholarly PDF is vendored in this repository. Original
BindScope source and synthetic data are covered by the root MIT license; that
license does not relicense external dependencies or the separate paper template.

Runtime dependencies used from the environment are Python and its sqlite3 and
ctypes modules; SQLite; and, for the unavailable native pilot, libpq. Optional
report rendering uses NumPy and Matplotlib. Consult the distributions' own
license files; upstream sources are:

- Python: https://docs.python.org/3/license.html
- SQLite: https://www.sqlite.org/copyright.html
- PostgreSQL/libpq: https://www.postgresql.org/about/licence/
- NumPy: https://numpy.org/doc/stable/license.html
- Matplotlib: https://matplotlib.org/stable/project/license.html

Two lookup shapes were independently adapted from public specifications, with
simplified names/data and no copied implementation:

- PostgreSQL pgbench select-only script:
  https://www.postgresql.org/docs/current/pgbench.html
- TechEmpower single-database-query specification:
  https://github.com/TechEmpower/FrameworkBenchmarks/wiki/Project-Information-Framework-Tests-Overview

The corpus is synthetic and does not contain either benchmark's downloaded
runtime/data. The model has ten further query shapes; the separate actual-engine corpus has
eight constructed shapes, and the timing corpus has four. All event histories
are original stress inputs. This is not a pgbench or TechEmpower benchmark submission.
