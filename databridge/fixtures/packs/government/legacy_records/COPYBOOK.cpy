      *****************************************************************
      * SYNTHETIC FIXTURE -- Walnut `government` pack                 *
      * LEGACY-MASTER record layout, ALDERMOOR TERRITORY DCSP         *
      * Extracted from the 1998 case system at the 2011 migration.    *
      * Fixed length 220 bytes, no record delimiter on the original   *
      * (EBCDIC, RECFM=FB LRECL=220 BLKSIZE=27500).  The copies in    *
      * ./data have been transcoded to ASCII and LF-terminated so     *
      * they can be read without a mainframe.                         *
      * NOTHING IN THIS FILE IS REAL.                                 *
      *****************************************************************
       01  LEGACY-MASTER-RECORD.
           05  LM-REC-TYPE         PIC X(02).    *>   1-  2
           05  LM-LEGACY-KEY       PIC X(12).    *>   3- 14
           05  LM-SURNAME          PIC X(30).    *>  15- 44
           05  LM-FORENAME         PIC X(24).    *>  45- 68
           05  LM-DOB-CYMD         PIC 9(08).    *>  69- 76
           05  LM-ADDR-LINE1       PIC X(40).    *>  77-116
           05  LM-ADDR-TOWN        PIC X(20).    *> 117-136
           05  LM-POSTCODE         PIC X(08).    *> 137-144
           05  LM-PARCEL-REF       PIC X(12).    *> 145-156
           05  LM-PERMIT-REF       PIC X(14).    *> 157-170
           05  LM-BENEFIT-REF      PIC X(12).    *> 171-182
           05  LM-STATUS-CD        PIC X(02).    *> 183-184
           05  LM-AMOUNT-VAL       PIC 9(09).    *> 185-193
           05  LM-AMOUNT-SIGN      PIC X(01).    *> 194-194
           05  LM-OPENED-CYMD      PIC 9(08).    *> 195-202
           05  LM-LAST-UPD-CYMD    PIC 9(08).    *> 203-210
           05  LM-SRC-OFFICE       PIC X(04).    *> 211-214
           05  FILLER              PIC X(06).    *> 215-220

      * LM-REC-TYPE values:
      *   PM  permit master -- one per permit ever issued by the 1998 system
      *   BN  benefit master -- one per assistance case under the pre-2014 scheme
      *   AD  address history -- superseded addresses, the only history that survived
      *   CN  contact log -- one line per counter visit
      *   XX  unclassified -- records the 2011 migration could not type
      *
      * LM-AMOUNT-SIGN: '+' credit, '-' debit, ' ' not applicable.
      * CYMD dates are zero-filled when unknown.  A minority of rows
      * carry impossible dates (e.g. 19980230) that the 1998 system
      * accepted; they are preserved here, not repaired.
