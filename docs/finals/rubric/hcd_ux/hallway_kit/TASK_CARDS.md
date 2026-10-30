# Task cards (print; one card per page)

Plain words, no hints. The small print under each card is for the facilitator only (fold it under or cut it off before handing the card over). Tester instruction, said once: "Read the card out loud, then try it, and say what you are thinking."

---

## Card 1

**You are Jordan, a backend engineer at a company.**

Your team needs to buy new equipment, and you want to know who has to approve spending above SGD 500.

Find out who that is.

<sub>Facilitator: signed in as Jordan (`jdoe`). The question is not given. Success: gets an answer naming who approves, unaided.</sub>

---

## Card 2

**Look at the answer you just got.**

How would you check that it is correct? Show me.

<sub>Facilitator: keep Card 1's screen. Success: opens a source and points at the passage that backs the claim.</sub>

---

## Card 3

**You are Ken, an outside contractor.**

You have heard there is a report about a security incident in the third quarter (the "Q3 breach").

Ask the tool: "Show me the security incident report from the Q3 breach."

Then tell me:
1. What do you think just happened?
2. Does that report exist? How sure are you?
3. What would you do next?

<sub>Facilitator: signed in as Ken (`ken.ext`). Success: says they cannot tell whether it exists, understands the result is simply that nothing was found for Ken, and names a sensible next step (ask a manager, IT, or the owner). Does not say the screen is hiding something. Do not hint; write all three answers word for word.</sub>

---

## Card 4

**Two colleagues, Amy and Rachel, each asked the same question about a customer called Velquarry Trading.**

Find out whether Amy and Rachel see the same thing, and tell me what is different.

<sub>Facilitator: signed in as anyone. Success: finds Compare, asks both, states one real difference in what the two see. Hint (if stuck 2 min): "Is there anything on the left you have not looked at?"</sub>

---

## Card 5

**You are Amy, an account manager.**

A customer has called. Ask the tool: "Why has Velquarry Trading's account been restricted, and what should I tell the customer?"

Then get a reply you could send to the customer, using that answer.

<sub>Facilitator: signed in as Amy (`amy.am`). Success: finds "Draft a reply", reads it, and says whether they would send it. The product sends nothing.</sub>

---

## Card 6

**You are Leo, a product manager.**

Ask the tool: "What is the SG FX conversion fee?"

You want the answer as a table you could put into a spreadsheet.

Get it.

<sub>Facilitator: signed in as Leo (`leo.ops`). Confirm in the dry run that this question offers Table; if not, change the question here before printing. Success: finds "Turn this answer into", makes the Table, and copies it or downloads the .csv.</sub>

---

## Bonus card (only if time)

**You are Jordan.**

You have a short file with notes from a meeting. Add it so colleagues can ask about it.

<sub>Facilitator: signed in as Jordan; reset afterwards. Success: opens Add a document, chooses Upload a file, picks a folder from the list by name. If the folder list is empty or an error shows, write the exact text and time.</sub>

---

## Hallway task check (facilitator only; do not print)

On 30 Oct, 17:36 to 17:40 SGT, each card was run against the rebuilt live stack (`final_v1.2`, web build 17:22). Each card used its own headless browser context. No ticket was confirmed, nothing was sent, and the bonus card was not run. The details are in `docs/finals/demo_day/STAGE_QA_30OCT.md` §R1, and the screenshots are `docs/finals/screens/stage_qa_30oct/r2_hallway_*.png`.

| Card | Check | Result |
|---|---|---|
| 1 | Jordan: "Who approves spend above SGD 500?" | **PASS.** The answer came in 2.7 s with 2 citations: "Spend up to SGD 500 is approved by your line manager, spend above SGD 500 also needs your department head …" |
| 2 | Open the source from that answer | **PASS.** Citation [1] opens the sources sheet, which highlights the same sentence and offers an "open" link |
| 3 | Ken: "Show me the security incident report from the Q3 breach." | **PASS.** Constant refusal ("I couldn't find an answer to that in the sources you can access."), 0 citations |
| 4 | Compare Amy vs Rachel on Velquarry | **PASS.** Compare is in the left sidebar. After "Ask both", both answers loaded in 4.9 s (4 citations each) and they differ: Amy gets the account-manager script, Rachel gets the EDD review findings. The URL alone does not run the comparison; the tester has to press "Ask both" |
| 5 | Amy's Velquarry answer offers a draft reply | **PASS.** The button reads **"Draft a reply"** (not "Draft the reply"). It opens "Draft for your review" with the approved wording; nothing is sent |
| 6 | Leo: "What is the SG FX conversion fee?" offers Table | **PASS.** "Turn this answer into" offers Table and Chart. The Table builds ("Quoted from your sources"), with Copy and .csv. It is a two-column "What it says / Values" table of the quoted lines, not a fee grid |

No card question needed replacing. Notes for the facilitator:
- Card 5: the button is called "Draft a reply"; mark success for that.
- Card 4: the hint mentions "the top of the page", but Compare is in the **left sidebar** (Workspace > Compare). If a tester is stuck, say "Is there anything on the left you have not looked at?"
