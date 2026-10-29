# ROI model

**Everything on this page is a projection.** The hours saved are not measured yet; the pilot measures
them ([PILOT_PLAN.md](PILOT_PLAN.md)). The arithmetic is checked by the test vectors in
[value_calculator.spec.json](value_calculator.spec.json).

## 1. Formula

```
annual value     = staff × hours saved per person per week × working weeks × loaded cost per hour
break-even (min) = annual cost ÷ (staff × working weeks × loaded cost per hour) × 60
net value        = annual value − annual cost − model cost per year
payback (weeks)  = annual cost ÷ (staff × hours saved per week × loaded cost per hour)
```

## 2. Inputs

| Input | Default | Where it comes from | Label |
|---|---|---|---|
| Staff | 600 | A mid-size Singapore fintech. Aspire's own dated post says 400+ employees ([Aspire blog](https://aspireapp.com/en-sg/blog/aspire-grows-revenue-3x-yoy-hits-profitability-3-months-after-closing-us-100m-series-c)); we do not claim Aspire's current headcount | [estimate] |
| Hours saved per person per week | 1.5 (base) | A small share of APQC's 2.8 h/week ([BusinessWire](https://www.businesswire.com/news/home/20211109005449/en)) and of the brief's ~30% of the week | [estimate] |
| Loaded cost per hour | US$31 | Near Singapore median pay incl. employer CPF: S$5,775/month × 12 ÷ (46 × 40 h) ÷ 1.30 SGD/USD ≈ US$29/h ([MOM](https://stats.mom.gov.sg/Pages/IncomeTimeSeries.aspx), figure via secondary sources) | [verify] figure, [estimate] FX |
| Working weeks | 46 | After leave and public holidays | [estimate] |
| Annual cost | US$100,000 | Deployment figure in `docs/tencent/SUBMISSION.md` §4. At the US$10/seat/month list price alone it is US$72,000 ([PRICING_GTM.md](PRICING_GTM.md)) | [estimate] |
| Model cost per year | about US$1,040 | [COST_PER_ANSWER.md](COST_PER_ANSWER.md) | [estimate] |

## 3. Break-even: 7 minutes a week

```
100,000 ÷ (600 × 46 × 31) = 100,000 ÷ 855,600 = 0.117 h = 7.0 minutes per person per week
```

- That is **4%** of the 2.8 hours APQC measured (7 ÷ 168 min).
- It is **1%** of the 12 hours a week implied by the brief's ~30% (7 ÷ 720 min).
- At the list price only (US$72,000): 72,000 ÷ 855,600 × 60 = **5.0 minutes**.

On stage: drag "minutes saved" down to 7 on `/value`. Seven minutes just misses (US$99,820 against US$100,000);
eight minutes clears it. That is the honest edge.

## 4. Low, base, high

The three cases are the ones published in `docs/tencent/SUBMISSION.md` §4.

| Case | Hours saved / person / week | Cost / hour | Annual value | Net of US$100k cost and ~US$1k model spend | Payback |
|---|---|---|---|---|---|
| Low | 0.5 | US$32 | 600 × 0.5 × 46 × 32 = **US$441,600** | US$340,562 | 10.4 weeks |
| Base | 1.5 | US$31 | 600 × 1.5 × 46 × 31 = **US$1,283,400** | US$1,182,362 | 3.6 weeks |
| High | 4.0 | US$37 | 600 × 4.0 × 46 × 37 = **US$4,084,800** | US$3,983,762 | 1.1 weeks |

Rounded for slides: US$0.44M / US$1.3M / US$4.1M.

## 5. Sensitivity (one input moved, the rest at base)

| Input moved | Low setting → value, break-even | High setting → value, break-even |
|---|---|---|
| Minutes saved (base 90) | 30 → US$427,800; break-even unchanged at 7.0 min | 240 → US$3,422,400; 7.0 min |
| Staff (base 600) | 300 → US$641,700; **14.0 min** | 1,200 → US$2,566,800; **3.5 min** |
| Cost per hour (base 31) | US$20 → US$828,000; **10.9 min** | US$45 → US$1,863,000; **4.8 min** |
| Working weeks (base 46) | 40 → US$1,116,000; **8.1 min** | 48 → US$1,339,200; **6.7 min** |
| Annual cost (base US$100k) | US$50k → value same; **3.5 min** | US$200k → value same; **14.0 min** |
| Model cost (base ~US$1k) | 50 answers a day, all long (12,000 in, 800 out) → 600 × 50 × 21 × 12 × US$0.0020 ≈ US$15,200 a year | About 1% of the base value |

What the table says:

1. **Hours saved is the only input that decides the answer.** Everything else moves break-even between
   3.5 and 14 minutes a week.
2. **Model cost does not matter** to the ROI. Connector API quotas and people's habits do
   ([RISK_REGISTER.md](RISK_REGISTER.md) R2, R12).
3. **The value we do not count:** avoided incidents. IBM puts the average financial-sector breach at about
   US$5.56M in 2025 ([Baker Donelson summary](https://www.bakerdonelson.com/ten-key-insights-from-ibms-cost-of-a-data-breach-report-2025), [secondary]).
   We leave it out, because we cannot measure it.

## 6. What the pilot measures to replace the projection

| Projection input | Pilot measurement |
|---|---|
| Hours saved | Median time-to-answer against a baseline of 20 sampled tickets; answer acceptance |
| Answers per day | Count from the ledger (`request.accepted` events per person per day) |
| Tokens per answer | TokenHub usage fields for every live Hy3 answer (needs a small change: the pipeline does not record usage today) |
| Cost per hour | Aspire's own loaded rate, given privately |

## 7. The in-app calculator

The UI agent builds `/value` from [value_calculator.spec.json](value_calculator.spec.json). It holds the
inputs, defaults, ranges, presets (low, base, high, "only 7 minutes", list price), output formulas,
reference lines (APQC, Atlassian, the brief) with links, accessibility rules and six test vectors.
The page does arithmetic in the browser only. It calls no backend and stores nothing.
