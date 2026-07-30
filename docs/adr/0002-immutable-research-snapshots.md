# Keep completed research snapshots immutable

Completed shopping research is stored and reopened as an immutable point-in-time snapshot with its
data and exchange-rate timestamps. Re-querying marketplaces creates a new task linked by user intent
instead of mutating the old result, trading storage for reproducible comparisons and an honest record
of what the shopper originally saw.
