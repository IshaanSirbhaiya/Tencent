# Pricing and go-to-market

**Everything here is an estimate** unless a source is linked. No price has been agreed with anyone.

## 1. Price and unit economics

| Item | Value | Note |
|---|---|---|
| List price | **US$10 per seat per month**, minimum 100 seats, plus a fixed pilot fee | [estimate] |
| Comparison | Glean is reported at about US$45-50 per seat | Third-party and competitor-authored ([GoSearch](https://www.gosearch.ai/blog/glean-pricing-explained)) [verify]. Do not state as fact |
| Customer break-even at list price, 600 seats | 72,000 ÷ (600 × 46 × 31) × 60 ≈ **5 min/person/week** | [ROI_MODEL.md](ROI_MODEL.md) §3 |
| Model cost per seat | about US$0.14-0.72 a month | [COST_PER_ANSWER.md](COST_PER_ANSWER.md) §3 |
| Infrastructure per seat at scale | about US$0.30-1.00 a month | [SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) §2 |
| Gross margin before support | about 80-95% | (10 − 0.14 − 1.00) ÷ 10 = 88.6% at the high infra end with light usage; support and implementation are the real cost |
| Self-hosted tier | Annual licence plus the customer's GPUs | For banks that want nothing to leave their tenant |

## 2. Go-to-market, in order

1. **Design partner: Aspire, internally.** One team, ten weeks, Singapore first, then other markets with
   market-scoped answers ([PILOT_PLAN.md](PILOT_PLAN.md)).
2. **Distribution through Tencent.** WorkBuddy as the front door (the MCP connector is built) and a Tencent
   Cloud Marketplace listing that keeps hosting in Singapore. [to explore with Tencent]
3. **Segment: MAS-regulated mid-size FIs** (roughly 200-2,000 staff) that cannot switch on a Copilot-style
   assistant because of AI-risk and TRM rules. Singapore has more than 1,800 fintech firms
   ([Mothership on MAS, Sep 2026](https://mothership.sg/2026/09/mas-fintech/), [secondary]); the qualifying
   share is unknown [verify].
4. **Later, possibly, via AspireOS banks.** Aspire targets 6,000+ commercial banks with AspireOS
   ([Aspire blog](https://aspireapp.com/en-sg/blog/aspire-launches-aspireos-its-ai-powered-platform-for-banks), [primary]).
   An internal-knowledge module for bank staff is an idea to raise *with* Aspire, not a claim.

## 3. Bottom-up sizing (illustrative only)

```
200 regulated SEA firms × 500 seats × US$10 × 12 = US$12M a year
```

The 200-firm count is an assumption, to be replaced with a count from MAS's financial institutions
directory [verify]. We do not use top-down market numbers.

## 4. Why buyers pay

FICO's Aug 2026 forum poll of 51 APAC bank executives: data privacy and security was the top AI barrier
(42%); cost was 16% ([Tech Coffee House](https://techcoffeehouse.com/2026/10/16/77-of-apac-banks-want-purpose-built-ai-fico/), [secondary], small sample).
So we lead with proof of safety, show cost as small and stable, and move on.
