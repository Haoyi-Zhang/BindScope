Superseded or unsuccessful runs; NEVER inputs to current paper reporting.

sqlite-unbarriered/: the original-SQL pilot without adequate parser-catalog
synchronization had real candidate/fresh differences even under the full guard.
It motivated the separate catalog_sync diagnostic and the shared three-catalog
step before each controlled pair. These failures were not relabeled as successes.

native-without-read-observer/: original-SQL measurements before adding independent
read observations; superseded by results/native_sqlite_* using the final observer.
authorizer-pilot/: a short instrumented development pilot, not the final budget.

pool-pilot/: early throughput pilot with unnecessary guard work in Checkout.
pool-fair-pilot/: a smaller pilot after correcting the baseline. Neither is used
for performance claims; the final plain baseline appears in results/native_pool.*.

legacy-first-delivery/: earlier SQLite bridge/pool measurements and old archive
checks from the previous delivery. They do not validate this revision or establish
actual original-SQL prepared-handle behavior. Environment fields remain original.

Timeout logs record interrupted tool invocations, not scored completed runs.
The final reproduction ran all declared stages sequentially and to completion.
The latest actual PostgreSQL failure is separately recorded one directory above
in postgres_pilot.json/log. No server execution is inferred from installation or
connection attempts. The final clean-copy check is stored above, not this folder.
