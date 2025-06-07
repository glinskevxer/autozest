# autozest

Consignment, block and settlement for a wholesale vehicle auction.

A unit arrives on a truck, gets inspected, runs through a lane, sells or does
not, and then a great deal of paperwork happens. This is the system that keeps
track of all of it: what was announced at the block, what that means for what a
buyer can come back on, who is owed what, and where the title is.

It is a command line and a SQLite file. There is no web front end and no
server, because a sale is one building on one night and the arbitration desk
needs to be able to copy the whole thing to a laptop.

## Installing

```
pip install .
az --db sale.sqlite3 init
```

## A sale, end to end

```
az --db sale.sqlite3 sale create 2024-03-12 --title "Tuesday dealer sale"
az --db sale.sqlite3 consign register 1HGCM82633A004352 d-100 --floor 5000.00
az --db sale.sqlite3 consign check-in 1HGCM82633A004352 --keys 2 --miles 41930
az --db sale.sqlite3 inspect open 1HGCM82633A004352 --inspector j.hale
az --db sale.sqlite3 inspect sign R-2024-000001 --value 30000.00
az --db sale.sqlite3 sale catalogue S-20240312 --lane A
az --db sale.sqlite3 sale ready S-20240312
az --db sale.sqlite3 sale open S-20240312
az --db sale.sqlite3 block bid S-20240312 A001 d-200 8200.00
az --db sale.sqlite3 block fall S-20240312 A001
az --db sale.sqlite3 settle post S-20240312
```

To see it working without typing any of that:

```
az --db demo.sqlite3 init
az --db demo.sqlite3 admin seed --units 24
az --db demo.sqlite3 report recap S-20240312
```

## The idea worth knowing

The announcements made at the block decide which arbitration claims are heard
later. A dented rail becomes an announcement, the announcement decides the
light the unit runs under, and the light decides what a buyer can come back on.
Nobody types that chain in; it falls out of an inspection and a floor price.

`az report reference` prints the whole rule set from the same objects the
system decides with, so it cannot be out of date.

## The commands

`az admin manual` writes the operator manual, generated from the command tree
itself. `az --help` lists the groups:

| group | what it is for |
| --- | --- |
| `consign` | Booking units in and receiving them at the gate |
| `inspect` | Condition reports, and signing them off |
| `sale` | Building next Tuesday and deciding whether it can open |
| `block` | The ring, live, while units are crossing |
| `arb` | The arbitration desk |
| `title` | Paperwork in and paperwork out |
| `settle` | Posting, taking money in, paying sellers out |
| `yard` / `gate` | Where the units are, keys, letting them off site |
| `roster` | Who is on which lane |
| `market` / `limits` | What a unit is worth, and caps on a buyer |
| `report` | Everything printed. Nothing here writes |
| `admin` | Backups, checks, the audit trail and the manual |

## Checking it over

```
az --db sale.sqlite3 admin verify
```

Runs the health checks, the schema doctor, the release checks and the advisory
list, and exits non zero only for the things that stop the building. Safe in a
cron job; it changes nothing.

## Working on it

```
pip install -e ".[dev]"
python -m ruff check . && python -m ruff format --check .
python -m mypy
python -m pytest -q --cov=src/autozest
python -m autozest.tools.check_migrations
python -m autozest.tools.release
```

Requires Python 3.12. Everything is pinned exactly, migrations are forward only
and numbered, and the coverage floor is 95.
