"""
scrape_racecard.py
Scrapes the nearest available HKJC local race card (today or upcoming date).
Outputs data/racecard_today.json.

Scheduled via GitHub Actions at 06:00 HKT each morning.
"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

DATA_DIR  = os.path.join(os.path.dirname(__file__), 'data')
OUT_FILE  = os.path.join(DATA_DIR, 'racecard_today.json')
CARD_URL  = 'https://racing.hkjc.com/zh-hk/local/information/racecard'

def hkt_today() -> str:
    """Return today's date string in YYYY/MM/DD (HKT = UTC+8)."""
    now_hk = datetime.now(timezone.utc) + timedelta(hours=8)
    return now_hk.strftime('%Y/%m/%d')

def hkt_today_date() -> date:
    """Return today's date object in HKT."""
    return datetime.strptime(hkt_today(), '%Y/%m/%d').date()

def parse_date_token(text: str) -> str | None:
    """Extract date token and normalize to YYYY/MM/DD."""
    if not text:
        return None

    m_ymd = re.search(r'(\d{4})[/-](\d{2})[/-](\d{2})', text)
    if m_ymd:
        y, m, d = m_ymd.groups()
        return f'{y}/{m}/{d}'

    m_dmy = re.search(r'(\d{2})/(\d{2})/(\d{4})', text)
    if m_dmy:
        d, m, y = m_dmy.groups()
        return f'{y}/{m}/{d}'

    return None

def to_ddmmyyyy(ymd: str) -> str:
    y, m, d = ymd.split('/')
    return f'{d}/{m}/{y}'

def is_local_hk_page(content: str) -> bool:
    return any(v in content for v in ('沙田', '跑馬地'))

def detect_venue(content: str) -> str | None:
    if '沙田' in content:
        return 'ST'
    if '跑馬地' in content:
        return 'HV'
    return None

def is_valid_entry(entry: dict) -> bool:
    horse_no = (entry.get('horse_no') or '').strip()
    horse_name = (entry.get('horse_name') or '').strip()
    jockey = (entry.get('jockey') or '').strip()
    trainer = (entry.get('trainer') or '').strip()

    # Exclude placeholder/imported-race rows such as "S1 日本:".
    if not re.fullmatch(r'\d+', horse_no):
        return False
    if not horse_name or not jockey or not trainer:
        return False
    return True

def extract_candidate_dates(content: str) -> list[dict[str, str]]:
    """Read race-date options from page and normalize candidate list."""
    soup = BeautifulSoup(content, 'html.parser')
    select = soup.find('select', id='selectId')
    if not select:
        return []

    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, str]] = []

    for option in select.find_all('option'):
        raw_value = (option.get('value') or '').strip()
        raw_label = option.get_text(' ', strip=True)

        ymd = parse_date_token(raw_value) or parse_date_token(raw_label)
        if not ymd:
            continue

        select_value = raw_value or to_ddmmyyyy(ymd)
        key = (select_value, ymd)
        if key in seen:
            continue
        seen.add(key)

        candidates.append({'date': ymd, 'value': select_value, 'label': raw_label})

    return candidates

def order_candidates(candidates: list[dict[str, str]], today_ref: date) -> list[dict[str, str]]:
    def d_obj(item: dict[str, str]) -> date:
        return datetime.strptime(item['date'], '%Y/%m/%d').date()

    future = sorted([c for c in candidates if d_obj(c) >= today_ref], key=d_obj)
    past = sorted([c for c in candidates if d_obj(c) < today_ref], key=d_obj, reverse=True)
    return future + past

def extract_race_numbers(soup: BeautifulSoup) -> list[str]:
    numbers = []
    for a in soup.select('.js_racecard a[href*="RaceNo="], a[href*="RaceNo="]'):
        href = a.get('href', '')
        m = re.search(r'RaceNo=(\d+)', href, re.I)
        if m:
            numbers.append(m.group(1))
    return sorted(list(set(numbers)), key=int)

def generate_date_search_order(today_ref: date, days_ahead: int = 7, days_back: int = 3) -> list[str]:
    """Generate candidate race dates, prioritizing upcoming days first."""
    candidates: list[str] = []

    for offset in range(0, days_ahead + 1):
        d = today_ref + timedelta(days=offset)
        candidates.append(d.strftime('%Y/%m/%d'))

    for offset in range(1, days_back + 1):
        d = today_ref - timedelta(days=offset)
        candidates.append(d.strftime('%Y/%m/%d'))

    return candidates

def extract_entries(race_soup: BeautifulSoup) -> list[dict]:
    entries: list[dict] = []

    entry_table = race_soup.find('table', class_=re.compile(r'draggable|raceCard', re.I))
    if not entry_table:
        for tbl in race_soup.find_all('table'):
            if tbl.find(string=re.compile(r'騎師|馬名')):
                entry_table = tbl
                break

    if not entry_table:
        return entries

    for row in entry_table.select('tr')[1:]:
        cols = row.select('td')
        if len(cols) < 6:
            continue

        candidate = {
            'horse_no': cols[0].get_text(strip=True),
            'horse_name': cols[1].get_text(strip=True),
            'jockey': cols[2].get_text(strip=True),
            'trainer': cols[3].get_text(strip=True),
            'weight': cols[4].get_text(strip=True),
            'gate': cols[5].get_text(strip=True),
        }
        if is_valid_entry(candidate):
            entries.append(candidate)

    return entries

async def switch_to_candidate_date(page, candidate: dict[str, str]) -> bool:
    """Switch racecard page to a specific candidate date via dropdown."""
    try:
        await page.wait_for_selector('#selectId', timeout=15000)
    except Exception:
        # If selector is missing, keep current page and try parsing anyway.
        return True

    selected = False
    value = candidate.get('value', '')
    label = candidate.get('label', '')
    date_ymd = candidate['date']

    if value:
        try:
            await page.select_option('#selectId', value=value)
            selected = True
        except Exception:
            pass

    if not selected and label:
        try:
            await page.select_option('#selectId', label=label)
            selected = True
        except Exception:
            pass

    if not selected:
        try:
            await page.select_option('#selectId', value=to_ddmmyyyy(date_ymd))
            selected = True
        except Exception:
            pass

    if not selected:
        print(f'Could not select date {date_ymd} from dropdown.')
        return False

    try:
        submit = await page.query_selector('#submitBtn')
        if submit:
            await submit.click()
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_selector('.js_racecard, .no-race-card, #errorMsg', timeout=20000)
        await asyncio.sleep(2)
    except Exception:
        pass

    return True

async def scrape_races_for_date(page, target_date: str, venue: str, race_nos: list[str]) -> list[dict]:
    """Scrape all races for one date and venue."""
    races = []
    race_date_dash = target_date.replace('/', '-')

    for race_no in race_nos:
        print(f'  Scraping race {race_no} ...')
        race_url = (
            f'https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx'
            f'?RaceDate={race_date_dash}&Racecourse={venue}&RaceNo={race_no}'
        )

        navigated = False
        selector = f'.js_racecard a[href*="RaceNo={race_no}"]'
        try:
            link = await page.query_selector(selector)
            if link:
                await link.click()
                await page.wait_for_load_state('domcontentloaded')
                await asyncio.sleep(1)
                navigated = True
        except Exception:
            pass

        if not navigated:
            try:
                await page.goto(race_url, wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(1)
            except Exception as e:
                print(f'  Could not navigate to race {race_no}: {e}')
                continue

        race_soup = BeautifulSoup(await page.content(), 'html.parser')
        text = race_soup.get_text()

        dist_m = re.search(r'(\d{3,4}米)', text)
        distance = dist_m.group(1) if dist_m else ''

        going = ''
        going_label = race_soup.find(string=re.compile(r'場地狀況'))
        if going_label:
            td = going_label.find_parent('td')
            if td:
                nxt = td.find_next_sibling('td')
                if nxt:
                    going = nxt.get_text(strip=True)

        entries = extract_entries(race_soup)
        if entries:
            races.append({
                'race_no': race_no,
                'distance': distance,
                'going': going,
                'entries': entries,
            })
            print(f'  Race {race_no}: {len(entries)} entries, dist={distance}, going={going}')
        else:
            print(f'  Race {race_no}: no valid local entries found, skipping.')

    return races

async def scrape_races_from_direct_seed(page, target_date: str, venue: str) -> list[dict]:
    """Try direct racecard URL for specific date/venue and scrape all races."""
    race_date_dash = target_date.replace('/', '-')
    seed_url = (
        f'https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx'
        f'?RaceDate={race_date_dash}&Racecourse={venue}&RaceNo=1'
    )

    try:
        await page.goto(seed_url, wait_until='domcontentloaded', timeout=35000)
        await asyncio.sleep(1)
    except Exception as e:
        print(f'  {target_date} {venue}: direct seed load failed: {e}')
        return []

    content = await page.content()
    if not is_local_hk_page(content):
        return []

    soup = BeautifulSoup(content, 'html.parser')
    race_nos = extract_race_numbers(soup)
    if not race_nos:
        print(f'  {target_date} {venue}: no race tabs found on page.')
        return []

    print(f'  {target_date} {venue}: candidate races {race_nos}')
    return await scrape_races_for_date(page, target_date, venue, race_nos)

def write_empty(reason: str = ''):
    payload = {'date': None, 'venue': None, 'scraped_at': datetime.utcnow().isoformat() + 'Z', 'races': []}
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'No race card written ({reason}).')

async def scrape():
    today_ref = hkt_today_date()
    print(f'Scraping race card around {today_ref.strftime("%Y/%m/%d")} ...')

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

        # Wait for race selector or an error/no-race notice.
        try:
            await page.wait_for_selector('.js_racecard, .no-race-card, #errorMsg', timeout=20000)
        except Exception:
            pass

        initial_content = await page.content()
        candidates = extract_candidate_dates(initial_content)

        # Fallback when dropdown is unavailable: try current HKT date.
        if not candidates:
            today = hkt_today()
            candidates = [{'date': today, 'value': to_ddmmyyyy(today), 'label': to_ddmmyyyy(today)}]

        ordered_candidates = order_candidates(candidates, today_ref)
        print('Candidate race dates:', ', '.join(c['date'] for c in ordered_candidates[:8]))

        tried_pairs: set[tuple[str, str]] = set()

        for candidate in ordered_candidates:
            target_date = candidate['date']
            print(f'\nTrying race date {target_date} ...')

            switched = await switch_to_candidate_date(page, candidate)
            if not switched:
                continue

            content = await page.content()
            if not is_local_hk_page(content):
                print(f'  {target_date}: not a Hong Kong local race page, skip.')
                continue

            venue = detect_venue(content)
            if not venue:
                print(f'  {target_date}: cannot detect venue, skip.')
                continue

            tried_pairs.add((target_date, venue))

            soup = BeautifulSoup(content, 'html.parser')
            race_nos = extract_race_numbers(soup)
            if not race_nos:
                print(f'  {target_date}: no race links found, skip.')
                continue

            print(f'  Found {len(race_nos)} races at venue {venue}: {race_nos}')
            races = await scrape_races_for_date(page, target_date, venue, race_nos)

            if not races:
                print(f'  {target_date}: no valid local entries extracted, trying next date.')
                continue

            payload = {
                'date': target_date,
                'venue': venue,
                'scraped_at': datetime.utcnow().isoformat() + 'Z',
                'races': races,
            }
            with open(OUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            await browser.close()
            print(f'\nDone. {len(races)} races written to {OUT_FILE}')
            return

        print('\nTrying direct date+venue fallback search ...')
        for target_date in generate_date_search_order(today_ref, days_ahead=7, days_back=3):
            for venue in ('ST', 'HV'):
                if (target_date, venue) in tried_pairs:
                    continue

                print(f'\nTrying direct {target_date} {venue} ...')
                races = await scrape_races_from_direct_seed(page, target_date, venue)
                if not races:
                    continue

                payload = {
                    'date': target_date,
                    'venue': venue,
                    'scraped_at': datetime.utcnow().isoformat() + 'Z',
                    'races': races,
                }
                with open(OUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)

                await browser.close()
                print(f'\nDone. {len(races)} races written to {OUT_FILE}')
                return

        await browser.close()

    write_empty('no local racecard with valid entries available')

if __name__ == '__main__':
    asyncio.run(scrape())
