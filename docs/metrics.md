# Metric Definitions

Every number FundXRay shows is defined here, with its formula, its inputs, and its limitations. If a metric cannot be defined precisely, it does not ship.

---

## 1. Look-through weight

The true weight of company *c* in a user's portfolio.

```
w(c) = Σ_f  [ V(f) / V_total ] × h(c, f)
```

| Symbol | Meaning |
|---|---|
| `f` | a fund the user holds |
| `V(f)` | user's rupee value in fund `f` |
| `V_total` | total portfolio value |
| `h(c, f)` | weight of company `c` inside fund `f`, from the latest disclosure |

**Inputs:** AMFI monthly portfolio disclosures, resolved to ISIN.
**Limitation:** holdings are disclosed monthly, so exposure is as of the last disclosure date, not today. Always display that date.

---

## 2. Pairwise portfolio overlap

Standard overlap between two schemes:

```
Overlap(A, B) = Σ_c min( h(c, A), h(c, B) )
```

Interpretation bands in common industry use: **< 30%** healthy, **30–50%** worth monitoring, **> 50%** the second fund is adding limited diversification.

**Note:** FundXRay reports this for compatibility with what users expect, but treats §1 look-through weight as the primary lens. Pairwise overlap understates the problem in a 6-fund portfolio.

---

## 3. Active share

Fraction of a fund's portfolio that differs from its benchmark.

```
ActiveShare(f) = ½ × Σ_c | h(c, f) − h(c, benchmark) |
```

Ranges from 0% (identical to index) to 100% (no common holdings).

**Closet index flag:** active share below a configured threshold while charging an actively-managed expense ratio. FundXRay presents the raw numbers and the fee attached; it does not label a fund "bad".

**Inputs:** scheme holdings + benchmark constituent weights.
**Limitation:** sensitive to benchmark selection; always display which benchmark was used.

---

## 4. Style drift

Composition of a scheme by market-cap bucket, using **AMFI's official classification** (top 100 = large cap, 101–250 = mid cap, 251+ = small cap), published half-yearly.

```
LargeCapShare(f, t) = Σ_{c ∈ Large(t)} h(c, f, t)
```

Charted month over month and compared against what the scheme's SEBI category permits.

**Limitation:** the AMFI list updates half-yearly, so classification is stepwise. Use the list in force at time `t`, not today's list — this is a common and serious error.

---

## 5. Fee drag

Terminal-value difference between two expense ratios over a horizon.

```
FV(TER) = Σ_{i=1}^{n}  C × (1 + (g − TER)/12) ^ (n − i)
Drag    = FV(TER_direct) − FV(TER_regular)
```

where `C` is the monthly contribution, `g` the gross annual return assumption, and `n` the number of months.

**Always display the return assumption.** Never present it as a prediction — it is a sensitivity illustration, and should be shown across a range of `g`.

---

## 6. Inferred turnover

Approximated from consecutive monthly disclosures:

```
Turnover(f, t) ≈ ½ × Σ_c | h(c, f, t) − h(c, f, t−1) |  × 12
```

**Limitation:** this is a lower bound. Intra-month round trips are invisible to monthly disclosure, and price movement alone changes weights without any trading. Adjust for price drift using SmartAPI historical closes before attributing change to trading, and label the result *inferred*.

---

## 7. Crowding — aggregate MF ownership

Share of a company held by the mutual fund industry in aggregate.

```
MFOwnership(c, t) = Σ_f  Units(c, f, t)  /  FreeFloatShares(c, t)
```

**Inputs:** all scheme disclosures for month `t`, plus free-float share count.

---

## 8. Days-to-Liquidate (DTL) — *flagship metric*

How many trading days the mutual fund industry would need to exit a position without dominating the tape.

```
ADV(c)  = mean daily traded volume of c over the trailing 30 sessions
DTL(c)  = TotalMFShares(c) / ( participation × ADV(c) )
```

with `participation` conventionally 20% of ADV — the rough level above which trading starts moving price.

**Why it matters.** A high DTL means the industry collectively cannot exit quickly. In a redemption wave, funds sell into each other through the same narrow door, and the realised price is far below the marked price. This is standard institutional liquidity-risk practice and is entirely computable from free public data — yet no consumer-facing Indian tool surfaces it.

**Inputs:** aggregate holdings (AMFI disclosures) × traded volume (Angel One SmartAPI historical candles).

**Limitations:** ADV is backward-looking and collapses precisely when it matters most; the 20% participation assumption is a convention, not a law; disclosure lag means holdings are up to a month stale. Present DTL as a *relative ranking* across stocks rather than a precise forecast of exit time.

---

## 9. Portfolio risk metrics

Computed on the look-through equity exposure using SmartAPI historical closes:

- **Realised volatility** — annualised standard deviation of daily log returns
- **Beta** — regression of portfolio returns against Nifty 50
- **Maximum drawdown** — largest peak-to-trough decline over the window
- **Concentration** — Herfindahl-Hirschman Index of look-through weights

---

## Presentation rules

1. Every metric displays its **as-of date** and **source**.
2. Assumptions (return rate, participation rate, benchmark) are **visible and adjustable**, never hidden.
3. Metrics are framed descriptively — *what is* — never prescriptively.
4. No metric implies a recommendation, ranking, or suitability judgement. See [`compliance.md`](compliance.md).
