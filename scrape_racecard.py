"""
scrape_racecard.py
Scrapes today's HKJC local race card (upcoming entries, before races start).
Outputs data/racecard_today.json.

Scheduled via GitHub Actions at 06:00 HKT each morning.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

DATA_DIR  = os.path.join(os.path.dirname(__file__), 'data')
OUT_FILE  = os.path.join(DATA_DIR, 'racecard_today.json')
CARD_URL  = 'https://racing.hkjc.com/zh-hk/local/information/racecard'

def hkt_today() -> str:
    """Return today's date string in YYYY/MM/DD (HKT = UTC+8)."""
    now_hk = datetime.now(timezone.utc) + timedelta(hours=8)
    return now_hk.strftime('%Y/%m/%d')

def write_empty(reason: str = ''):
    payload = {'date': None, 'venue': None, 'scraped_at': datetime.utcnow().isoformat() + 'Z', 'races': []}
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'No race card written ({reason}).')

async def scrape():
    today = hkt_today()
    print(f'Scraping race card for {today} ...')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/119.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        try:
            await page.goto(CARD_URL, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f'Failed to load race card page: {e}')
            await browser.close()
            write_empty('page load failed')
            return

        # Wait for race selector or an error/no-race notice
        try:
            await page.wait_for_selector('.js_racecard, .no-race-card, #errorMsg', timeout=20000)
        except Exception:
            pass  # continue and check content

        content = await page.content()

        # Check venue — only process Hong Kong local races
        if not any(v in content for v in ['沙田', '跑馬地']):
            await browser.close()
            write_empty('no Hong Kong local race today')
            return

        venue = 'ST' if '沙田' in content else 'HV'
        soup  = BeautifulSoup(content, 'html.parser')

        # Discover race numbers from the race-tab navigation
        race_links = soup.select('.js_racecard a[href*="RaceNo="]')
        race_nos   = sorted(
            list(set(re.search(r'RaceNo=(\d+)', a['href'], re.I).group(1) for a in race_links)),
            key=int
        )
        if not race_nos:
            await browser.close()
            write_empty('no race links found on page')
            return

        print(f'Found {len(race_nos)} races at venue {venue}: {race_nos}')

        races = []
        for race_no in race_nos:
            print(f'  Scraping race {race_no} ...')
            # Navigate to specific race
            race_url = (
                f'https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx'
                f'?RaceDate={today.replace("/", "-")}&Racecourse={venue}&RaceNo={race_no}'
            )
            try:
                selector = f'.js_racecard a[href*="RaceNo={race_no}"]'
                link = await page.query_selector(selector)
                if link:
                    await link.click()
                    await page.wait_for_load_state('domcontentloaded')
                    await asyncio.sleep(1)
                else:
                    await page.goto(race_url, wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(1)
            except Exception as e:
                print(f'  Could not navigate to race {race_no}: {e}')
                continue

            race_soup = BeautifulSoup(await page.content(), 'html.parser')
            text      = race_soup.get_text()

            # Distance
            dist_m = re.search(r'(\d{3,4}米)', text)
            distance = dist_m.group(1) if dist_m else ''

            # Going — look for 場地狀況 label
            going = ''
            going_label = race_soup.find(string=re.compile(r'場地狀況'))
            if going_label:
                td = going_label.find_parent('td')
                if td:
                    nxt = td.find_next_sibling('td')
                    if nxt:
                        going = nxt.get_text(strip=True)

            # Entry table — try common HKJC card table selectors
            entries = []
            entry_table = race_soup.find('table', class_=re.compile(r'draggable|raceCard', re.I))
            if not entry_table:
                # Fallback: any table with expected columns
                for tbl in race_soup.find_all('table'):
                    if tbl.find(string=re.compile(r'騎師|馬名')):
                        entry_table = tbl
                        break

            if entry_table:
                rows = entry_table.select('tr')[1:]  # skip header
                for row in rows:
                    cols = row.select('td')
                    if len(cols) < 6:
                        continue
                    # HKJC card columns: 馬號, 馬名, 騎師, 練馬師, 實際負磅, 檔位 (order may vary)
                    # Try to extract by position; adjust if HKJC changes layout
                    try:
                        entries.append({
                            'horse_no':   cols[0].get_text(strip=True),
                            'horse_name': cols[1].get_text(strip=True),
                            'jockey':     cols[2].get_text(strip=True),
                            'trainer':    cols[3].get_text(strip=True),
                            'weight':     cols[4].get_text(strip=True),
                            'gate':       cols[5].get_text(strip=True),
                        })
                    except IndexError:
                        continue

            if entries:
                races.append({
                    'race_no':  race_no,
                    'distance': distance,
                    'going':    going,
                    'entries':  entries,
                })
                print(f'  Race {race_no}: {len(entries)} entries, dist={distance}, going={going}')
            else:
                print(f'  Race {race_no}: no entries found, skipping.')

        await browser.close()

    if not races:
        write_empty('no entry data extracted')
        return

    payload = {
        'date':        today,
        'venue':       venue,
        'scraped_at':  datetime.utcnow().isoformat() + 'Z',
        'races':       races,
    }
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f'\nDone. {len(races)} races written to {OUT_FILE}')

if __name__ == '__main__':
    asyncio.run(scrape())
