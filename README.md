# HKJC Race Results Scraper

A Python scraper that fetches Hong Kong Jockey Club (HKJC) local race results and saves them as CSV files.

## Features

- Scrapes race results from the [HKJC racing results page](https://racing.hkjc.com/zh-hk/local/information/localresults)
- Filters for **Hong Kong local races only** (Sha Tin 沙田 & Happy Valley 跑馬地)
- Saves each race day as a separate CSV file: `data/hkjc_results_YYYYMMDD.csv`
- Supports single-date scraping and bulk date-range scraping
- Retries automatically on network errors

## Requirements

- Python 3.9+
- [Playwright](https://playwright.dev/python/) (Chromium)
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)

```bash
pip install playwright beautifulsoup4
playwright install chromium
```

## Usage

### Scrape the most recent race dates automatically

```bash
python hkjc_scraper_github.py
```

Fetches the latest available race dates from the HKJC dropdown and scrapes any that have not yet been saved locally.

### Scrape a specific date range

```bash
python scrape_range.py
```

Edit the start/end dates inside `scrape_range.py` to backfill historical results.

## Output Format

Each CSV file contains one row per horse per race, with the following 14 columns:

| Column | Description |
|--------|-------------|
| 日期 | Race date (YYYY/MM/DD) |
| 場次 | Race number |
| 路程 | Distance (e.g. 1200米) |
| 場地狀況 | Track going (e.g. 好地) |
| 名次 | Finishing position (integer or "PU") |
| 馬號 | Saddle cloth / draw number |
| 馬名 | Horse name (Chinese) + code, e.g. `本能(K289)` |
| 騎師 | Jockey name |
| 練馬師 | Trainer name |
| 實際負磅 | Actual weight carried (lbs) |
| 排位體重 | Declared horse body weight (lbs) |
| 檔位 | Gate number |
| 完成時間 | Finish time |
| 賽後馬匹狀況 | Post-race incident / stewards report |

## Data

Scraped CSV files are stored in the `data/` directory and are consumed by the [hkjc_horse_viewer](https://github.com/mzf3334-dev/hkjc_horse_viewer) project.
