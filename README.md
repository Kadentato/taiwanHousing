# Taiwan Housing Explorer

A website for poking around Taiwan's housing market, built from the government's public
**Actual Price Registration (實價登錄 / LVR)** records. The whole thing runs in your browser, with no login
and no backend to keep alive.

**Live site → https://kadentato.github.io/taiwanHousing/**

![An animated map of Taiwan sweeping quarter by quarter from 2013 to 2026, each district shaded by its trailing-12-month median housing sale price in constant 2021 NT$ on a fixed colour scale — the Taipei area starts red and the rest of the west coast steadily warms as real prices climb about 50% over the period.](spatialAnalysis/taiwanPriceTimelapse.gif)

*One frame per quarter, each district shaded by its median over the previous 12 months, **adjusted for inflation to constant 2021 NT$** on a fixed scale. So a district going redder means prices actually rose in real terms, not that money got cheaper or that I recoloured the legend. Nationally the median went up about **+49% in real terms** from 2013 to 2025. The headline nominal figure is +75%, so roughly a third of that "rise" was just inflation. ([static all-years version](spatialAnalysis/taiwanPriceMap.png))*

## What it is

By law, every property sale in Taiwan has to be reported to the government, and it all gets published as
open data. The catch is that the raw files are a pain to work with: they're in Mandarin, split across a pile
of CSVs you have to stitch together, and not really meant to be read by a human. This site takes the full
history, cleans it up, translates it, and puts it on a map you can click around — about **3.5 million
housing sales from 2012 to 2026**.

## What's on the site

**🗺️ The map** — Prices by area, from the whole country down to single homes. Click from region to city to
district, and for **Taipei, New Taipei, Taichung, Taoyuan and Tainan** the individual sales show up at their
**real street addresses** (I geocoded them from the government's address data). Hover a dot to see that
home's price, size, layout, age and features. There's also a time chart going back to 2012 and a records
table you can sort and download as a CSV.

**🔮 The price predictor** — Describe a home (where it is, how big, how old, its features) and you get an
estimated price plus 50% / 80% / 95% ranges, so you can see how sure the model actually is. It's a
gradient-boosted model that runs entirely in your browser, so nothing you type gets sent anywhere.

**🔎 Browse the data** — The actual dataset, loaded straight into your browser, with every table and column
and an SQL box if you want to run your own queries.

## What you can do with it

- **House hunting, or just being nosy** — see what homes in a neighbourhood actually sold for, compare which
  districts are expensive versus cheap, and get a rough idea of what a given place might be worth.
- **Learning stats** — it's a big, real dataset with a genuine time component, which makes it handy for
  practising trend analysis, medians versus means, confidence intervals and spatial patterns. There's a tidy
  monthly CSV too if you'd rather pull it into pandas or R.
- **Getting a feel for the market** — long-run price trends by area and property type, how far things have
  moved, and where the activity actually is.

## About the data (and some honesty)

The site uses the **full LVR history, 2012 Q3 → 2026 Q2, housing sales only (~3.5M de-duplicated deals)**.
A few things worth knowing before you read too much into it:

- **Prices are nominal NT$**, taken straight from the registry. The most recent months always look a little
  low, because sales get disclosed in batches with a lag.
- **Exact locations only exist for the five biggest metros — Taipei, New Taipei, Taichung, Taoyuan and
  Tainan** (roughly 70–90% of their sales). Everywhere else the dots scatter within the district, since the
  source doesn't give out coordinates there yet.
- **The predictor is an estimate, not an appraisal.** A lot of what makes one home cost more than another —
  the renovation, the exact floor, the view, how the haggling went — just isn't in the public data. Treat
  the ranges as a ballpark, and please don't use it as financial advice.

Want the details? [`dataDictionary.md`](dataDictionary.md) explains every field, and
[`modelCard.md`](modelCard.md) covers how the predictor works, how accurate it is, and where it shouldn't be
trusted.

## How it's built

It's a static site — just HTML, CSS and JavaScript reading pre-computed data files — so it hosts for free on
GitHub Pages, stays up with nothing to run, and every push redeploys it. If you want to run or rebuild it
yourself, [`deploymentGuide.md`](deploymentGuide.md) has the steps.

## Credits

Data: Ministry of the Interior, Taiwan — Real Estate Actual Price Registration (不動產成交案件實際資訊).
Map boundaries: ronnywang/twgeojson. Map tiles: © OpenStreetMap contributors.
