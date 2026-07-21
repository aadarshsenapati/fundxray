# ADR 0001 — Apache Iceberg over Hive tables

**Status:** accepted

**Context.** Warehouse tables must absorb a decade of changing AMC disclosure formats, support correction of historical months, and let readers query safely while writes are in flight.

**Decision.** Use Apache Iceberg tables on object storage, catalogued in the Hive Metastore.

**Rationale.** Schema evolution without rewriting data; hidden partitioning removes a whole class of user error; snapshot isolation means the API never reads a half-written month; time travel makes "what did we show on this date, and why" answerable — which matters when the numbers concern people's savings.

**Consequences.** Compaction and snapshot expiry must be scheduled explicitly. Hive-table equivalents are retained in the benchmark suite for comparison.
