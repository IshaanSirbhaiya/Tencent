# LEGACY-MASTER -- record layout

> SYNTHETIC FIXTURE. Derived from `LEGACY_FIELDS` in `../generate.py`;
> `COPYBOOK.cpy` and the `.dat` shards come from the same table, so the
> three cannot drift.

Fixed record length **220 bytes**, LF-terminated in this
repo (the original has no delimiter). Every field is space- or zero-padded
and uppercase, because the source system had no lower case.

| Field | Picture | Start | End | Width | Notes |
|---|---|---:|---:|---:|---|
| `LM-REC-TYPE` | `PIC X(02)` | 1 | 2 | 2 |  |
| `LM-LEGACY-KEY` | `PIC X(12)` | 3 | 14 | 12 |  |
| `LM-SURNAME` | `PIC X(30)` | 15 | 44 | 30 | **hard-truncated at 30** -- longer/hyphenated names lose their tail |
| `LM-FORENAME` | `PIC X(24)` | 45 | 68 | 24 | hard-truncated at 24; middle names were rarely captured |
| `LM-DOB-CYMD` | `PIC 9(08)` | 69 | 76 | 8 | `00000000` when unknown; impossible dates survive |
| `LM-ADDR-LINE1` | `PIC X(40)` | 77 | 116 | 40 |  |
| `LM-ADDR-TOWN` | `PIC X(20)` | 117 | 136 | 20 |  |
| `LM-POSTCODE` | `PIC X(08)` | 137 | 144 | 8 |  |
| `LM-PARCEL-REF` | `PIC X(12)` | 145 | 156 | 12 | loose join to `../gis_parcels`; pre-2004 refs use a retired scheme |
| `LM-PERMIT-REF` | `PIC X(14)` | 157 | 170 | 14 | the 1998 permit number, unrelated to today's `AM-P-YYYY-NNNNNN` |
| `LM-BENEFIT-REF` | `PIC X(12)` | 171 | 182 | 12 | the pre-2014 assistance reference, unrelated to today's BRN |
| `LM-STATUS-CD` | `PIC X(02)` | 183 | 184 | 2 |  |
| `LM-AMOUNT-VAL` | `PIC 9(09)` | 185 | 193 | 9 | minor units, zero-filled, unsigned -- the sign is the next byte |
| `LM-AMOUNT-SIGN` | `PIC X(01)` | 194 | 194 | 1 |  |
| `LM-OPENED-CYMD` | `PIC 9(08)` | 195 | 202 | 8 |  |
| `LM-LAST-UPD-CYMD` | `PIC 9(08)` | 203 | 210 | 8 |  |
| `LM-SRC-OFFICE` | `PIC X(04)` | 211 | 214 | 4 |  |
| `FILLER` | `PIC X(06)` | 215 | 220 | 6 | reserved; a few rows have junk here from a 2003 patch |

## Record types

| Code | Meaning |
|---|---|
| `PM` | permit master -- one per permit ever issued by the 1998 system |
| `BN` | benefit master -- one per assistance case under the pre-2014 scheme |
| `AD` | address history -- superseded addresses, the only history that survived |
| `CN` | contact log -- one line per counter visit |
| `XX` | unclassified -- records the 2011 migration could not type |

## Why this source is T3

There is no version history, no tombstone, no cursor and no change feed.
The file is re-cut in full by an overnight job; a record that disappears
between cuts is indistinguishable from one that was never there. Any
pointer into it must resolve against a captured copy in the Capture Store,
never against the live path.
