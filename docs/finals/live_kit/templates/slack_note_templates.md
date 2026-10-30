# Slack note templates (fill in the blanks, type live)

Rules: sign in as someone who is a MEMBER of the channel; at most 2,000 characters; plain text;
no real names, no real card numbers (a card number is masked at ingest anyway). Keep one clear fact
per note and keep the judge's own words, so the answer visibly echoes them.

| Pattern | Fill-in-the-blank | Good channels (persona must be a member) |
|---|---|---|
| Schedule | `Update on <TICKET>: <WHAT> is <STATE>; it happens <DAY DATE TIME TZ>. Contact: <PERSON>.` | #db-migration (jdoe, priya.sre), #sre-oncall (priya.sre), #releases (dev.p) |
| Announcement | `<TEAM>: <WHAT> is <DAY DATE>. <ONE ACTION FOR READERS>.` | #hr-announcements (melissa.k), #it-helpdesk (admin.it), #office-sg (any staff) |
| Correction | `Correction: <OLD FACT> is now <NEW FACT>, effective <DATE>.` | the channel where the old fact lives |
| Decision | `Decision: <WHAT> is approved by <WHO>; owner <PERSON>; due <DATE>.` | #product (hana.y, leo.ops), #payments-eng (jdoe) |
| Restricted | `<CASE-ID> update: <STATUS> for <MADE-UP COMPANY>. <NEXT STEP> on <DATE>.` | #fincrime-cases (mlro.tan) -- Amy and Ken will see nothing |

Pick the proof phrase BEFORE typing: 3 to 5 words from the note that appear nowhere else. Check a judge's
phrase in 5 seconds:
`python docs/finals/live_kit/check_absent.py --phrase "standby vpn token" --question "When does the standby VPN token expire?" --as jdoe`
(exit 0 = absent from the world and the question is refused). Or just ask the question on stage first
and let it refuse; that is the live absence proof.

Do not use: a customer name from the real world, a real person, anything that reads as a real
STR/SAR, or a real card/NRIC number.
