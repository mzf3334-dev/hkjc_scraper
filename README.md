# HKJC Race Results Scraper

Download Hong Kong race results and save them as CSV files.

## Install

You need Python 3.9 or later. Install the required packages and browser:

```bash
pip install playwright beautifulsoup4
playwright install chromium
```

## Get Recent Results

Run:

```bash
python hkjc_scraper_github.py
```

The scraper checks recent race dates and saves results that are not already on your computer.

## Get Results for a Date Range

Set the start and end dates in `scrape_range.py`, then run:

```bash
python scrape_range.py
```

## Output

CSV files are saved in the `data/` folder. Each row describes one horse in one race. The columns are date, race number, distance, track condition, finishing position, horse number, horse name, jockey, trainer, carried weight, declared horse weight, gate, finish time, and post-race notes.

The [HKJC Horse Viewer](https://github.com/mzf3334-dev/hkjc_horse_viewer) uses these files.
