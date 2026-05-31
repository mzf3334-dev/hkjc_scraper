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
from urllib.parse import unquote

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

DATA_DIR  = os.path.join(os.path.dirname(__file__), 'data')
OUT_FILE  = os.path.join(DATA_DIR, 'racecard_today.json')
CARD_URL    = 'https://racing.hkjc.com/zh-hk/local/information/racecard'
CARD_URL_EN = 'https://racing.hkjc.com/en/local/information/racecard'

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
    """Read race-date candidates from dropdown and race links."""
    soup = BeautifulSoup(content, 'html.parser')

    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, str]] = []

    def add_candidate(ymd: str | None, value: str = '', label: str = '') -> None:
        if not ymd:
            return
        select_value = value.strip() if value else to_ddmmyyyy(ymd)
        key = (select_value, ymd)
        if key in seen:
            return
        seen.add(key)
        candidates.append({'date': ymd, 'value': select_value, 'label': label.strip()})

    select = soup.find('select', id='selectId')
    if select:
        for option in select.find_all('option'):
            raw_value = (option.get('value') or '').strip()
            raw_label = option.get_text(' ', strip=True)

            ymd = parse_date_token(raw_value) or parse_date_token(raw_label)
            add_candidate(ymd, value=raw_value, label=raw_label)

    # Fallback/additional source: race links usually contain racedate=YYYY/MM/DD.
    for a in soup.select('a[href*="racedate=" i], a[href*="RaceDate=" i]'):
        href = a.get('href', '')
        m = re.search(r'(?:racedate|RaceDate)=([^&#]+)', href, re.I)
        if not m:
            continue

        raw_date = unquote(m.group(1)).strip()
        ymd = parse_date_token(raw_date)
        add_candidate(ymd)

    return candidates

def order_candidates(candidates: list[dict[str, str]], today_ref: date) -> list[dict[str, str]]:
    def d_obj(item: dict[str, str]) -> date:
        return datetime.strptime(item['date'], '%Y/%m/%d').date()

    future = sorted([c for c in candidates if d_obj(c) >= today_ref], key=d_obj)
    past = sorted([c for c in candidates if d_obj(c) < today_ref], key=d_obj, reverse=True)
    return future + past

def extract_race_numbers(soup: BeautifulSoup, venue: str = '') -> list[str]:
    """Extract race tab numbers for venue; always includes race 1."""
    numbers: set[str] = set()
    for a in soup.select('a[href*="RaceNo="]'):
        href = a.get('href', '')
        # Only consider SPA race-tab links (relative, start with '?racedate=')
        if not re.match(r'\?racedate=', href, re.I):
            continue
        if venue and f'Racecourse={venue}' not in href:
            continue
        m = re.search(r'RaceNo=(\d+)', href, re.I)
        if m:
            numbers.add(m.group(1))
    result = sorted(numbers, key=int)
    # Race 1 is the default page view — add it when absent from the tab links
    if result and '1' not in result:
        result = ['1'] + result
    return result

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

# Maps Chinese header text → internal field name
_HEADER_FIELD: dict[str, str] = {
    '馬匹編號': 'horse_no',
    '馬名':     'horse_name',
    '騎師':     'jockey',
    '練馬師':   'trainer',
    '負磅':     'weight',
    '檔位':     'gate',
}

# Confirmed fallback positions (diagnostic 2026-05-30):
# [0]=馬匹編號 [1]=6次近績 [2]=綵衣(img) [3]=馬名 [4]=烙號
# [5]=負磅     [6]=騎師   [7]=可能超磅   [8]=檔位 [9]=練馬師
_FALLBACK_COL_MAP: dict[str, int] = {
    'horse_no': 0, 'horse_name': 3, 'weight': 5,
    'jockey':   6, 'gate':       8, 'trainer': 9,
}


def extract_entries(race_soup: BeautifulSoup) -> list[dict]:
    entries: list[dict] = []

    # Use exact class matching to avoid matching 'js_racecard' via substring.
    entry_table = (race_soup.find('table', class_='starter') or
                   race_soup.find('table', class_='draggable'))
    if not entry_table:
        for tbl in race_soup.find_all('table'):
            if tbl.find(string=re.compile(r'騎師|馬名')):
                entry_table = tbl
                break

    if not entry_table:
        return entries

    rows = entry_table.find_all('tr')
    if not rows:
        return entries

    # Build column index map from header row
    col_map: dict[str, int] = {}
    for i, cell in enumerate(rows[0].find_all(['th', 'td'])):
        key = cell.get_text(strip=True)
        if key in _HEADER_FIELD:
            col_map[_HEADER_FIELD[key]] = i

    if not col_map:
        col_map = _FALLBACK_COL_MAP

    # Detect silk (綵衣) image column index
    silk_col_idx = 2  # confirmed fallback position
    for i, cell in enumerate(rows[0].find_all(['th', 'td'])):
        if '綵衣' in cell.get_text(strip=True):
            silk_col_idx = i
            break

    min_cols = max(col_map.values()) + 1

    for row in rows[1:]:
        cols = row.find_all('td')
        if len(cols) < min_cols:
            continue

        candidate = {
            field: cols[idx].get_text(strip=True)
            for field, idx in col_map.items()
        }
        # Extract jockey silk image URL from the 綵衣 column
        if len(cols) > silk_col_idx:
            img_tag = cols[silk_col_idx].find('img')
            if img_tag:
                src = (img_tag.get('src') or img_tag.get('data-original') or
                       img_tag.get('data-src') or '')
                if src.startswith('/'):
                    src = 'https://racing.hkjc.com' + src
                if src:
                    candidate['silk_url'] = src
        if is_valid_entry(candidate):
            entries.append(candidate)

    return entries

async def switch_to_candidate_date(page, candidate: dict[str, str]) -> bool:
    """Switch racecard page to a specific candidate date via dropdown."""
    try:
        await page.wait_for_selector('#selectId', timeout=3000)
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
    """Scrape all races by navigating to each race URL directly."""
    races = []

    for race_no in race_nos:
        print(f'  Scraping race {race_no} ...')
        race_url = (f'{CARD_URL}?racedate={target_date}'
                    f'&Racecourse={venue}&RaceNo={race_no}')
        try:
            await page.goto(race_url, wait_until='domcontentloaded', timeout=35000)
        except Exception as e:
            print(f'  Race {race_no}: page load failed: {e}')
            continue

        # Wait for entry table to render (SPA renders asynchronously)
        try:
            await page.wait_for_selector('table.starter', timeout=15000)
        except Exception:
            await asyncio.sleep(5)

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

        # If any entries are missing silk_url, supplement from English racecard
        # (English horse names carry the owner's color code, e.g. "HORSE NAME (B350)")
        if entries and any(not e.get('silk_url') for e in entries):
            en_url = (f'{CARD_URL_EN}?racedate={target_date}'
                      f'&Racecourse={venue}&RaceNo={race_no}')
            try:
                await page.goto(en_url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_selector('table.starter', timeout=12000)
                en_soup = BeautifulSoup(await page.content(), 'html.parser')
                en_entries = extract_entries(en_soup)
                en_silk: dict[str, str] = {}
                for en_e in en_entries:
                    hno = en_e.get('horse_no', '')
                    silk = en_e.get('silk_url', '')
                    if not silk:
                        m = re.search(r'\(([^)]+)\)\s*$', en_e.get('horse_name', ''))
                        if m:
                            silk = ('https://racing.hkjc.com/racing/content/'
                                    f'Images/RaceColor/{m.group(1)}.gif')
                    if hno and silk:
                        en_silk[hno] = silk
                for e in entries:
                    if not e.get('silk_url') and e.get('horse_no') in en_silk:
                        e['silk_url'] = en_silk[e['horse_no']]
            except Exception as ex:
                print(f'  Race {race_no}: English silk lookup failed: {ex}')

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
    """Try SPA racecard URL for specific date/venue and scrape all races."""
    seed_url = f'{CARD_URL}?racedate={target_date}&Racecourse={venue}&RaceNo=1'

    try:
        await page.goto(seed_url, wait_until='domcontentloaded', timeout=35000)
    except Exception as e:
        print(f'  {target_date} {venue}: direct seed load failed: {e}')
        return []

    # Wait for entry table to render (SPA renders asynchronously)
    try:
        await page.wait_for_selector('table.starter', timeout=15000)
    except Exception:
        await asyncio.sleep(5)

    content = await page.content()
    if not is_local_hk_page(content):
        return []

    soup = BeautifulSoup(content, 'html.parser')
    # Read actual date shown by SPA (may differ from target_date if params ignored)
    actual_date = target_date
    for a in soup.select(f'a[href*="Racecourse={venue}"][href*="RaceNo="]'):
        href = a.get('href', '')
        if not re.match(r'\?racedate=', href, re.I):
            continue
        m = re.search(r'racedate=([^&]+)', href, re.I)
        if m:
            actual_date = parse_date_token(unquote(m.group(1))) or target_date
            break

    race_nos = extract_race_numbers(soup, venue)
    if not race_nos:
        print(f'  {target_date} {venue}: no race tabs found on page.')
        return []

    print(f'  {target_date} {venue}: candidate races {race_nos} (actual date {actual_date})')
    return await scrape_races_for_date(page, actual_date, venue, race_nos)

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

        loaded = False
        for attempt in range(1, 4):
            try:
                await page.goto(CARD_URL, wait_until='domcontentloaded', timeout=60000)
                loaded = True
                break
            except Exception as e:
                print(f'Initial page load attempt {attempt} failed: {e}')
                if attempt < 3:
                    await asyncio.sleep(5)
        if not loaded:
            await browser.close()
            write_empty('page load failed after 3 attempts')
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
            race_nos = extract_race_numbers(soup, venue)
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
