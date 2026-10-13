"""Parse shared result fields from HKJC race pages."""

import re


_COLUMN_FALLBACKS = {
    '名次': 0,
    '馬號': 1,
    '馬名': 2,
    '騎師': 3,
    '練馬師': 4,
    '實際負磅': 5,
    '排位體重': 6,
    '檔位': 7,
    '頭馬距離': 8,
    '沿途走位': 9,
    '完成時間': 10,
    '獨贏賠率': 11,
}


def _compact(text):
    return re.sub(r'\s+', '', text or '').rstrip(':：')


def _labeled_value(soup, label):
    for cell in soup.find_all(['td', 'th']):
        if _compact(cell.get_text()) != label:
            continue
        value_cell = cell.find_next_sibling(['td', 'th'])
        if value_cell:
            return value_cell.get_text(strip=True)
    return ''


def _column_value(cells, column_indexes, name):
    index = column_indexes.get(name, _COLUMN_FALLBACKS[name])
    if index >= len(cells):
        return ''
    return cells[index].get_text(strip=True)


def extract_result_rows(race_soup, date_str, race_no):
    race_text = race_soup.get_text(' ', strip=True)
    distance_match = re.search(r'(\d+米)', race_text)
    distance = distance_match.group(1) if distance_match else ''
    going = _labeled_value(race_soup, '場地狀況')
    track = _labeled_value(race_soup, '賽道')

    class_match = re.search(
        r'((?:第\s*[一二三四五六七八九十\d]+\s*班)|新馬|[一二三123]級賽)'
        r'\s*[-－–—]\s*\d+\s*米',
        race_text,
    )
    race_class = class_match.group(1) if class_match else ''

    incidents = {}
    incident_table = next(
        (table for table in race_soup.find_all('table') if '競賽事件' in table.get_text()),
        None,
    )
    if incident_table:
        for row in incident_table.select('tr')[1:]:
            cells = row.select('td')
            if len(cells) >= 4:
                horse = cells[2].get_text(strip=True).split('(')[0].strip()
                incidents[horse] = cells[3].get_text(strip=True)

    result_table = race_soup.find('table', class_=re.compile(r'draggable'))
    if not result_table:
        return []

    table_rows = result_table.select('tr')
    if not table_rows:
        return []
    column_indexes = {
        _compact(cell.get_text()): index
        for index, cell in enumerate(table_rows[0].find_all(['th', 'td']))
    }

    results = []
    for row in table_rows[1:]:
        cells = row.select('td')
        if len(cells) < len(_COLUMN_FALLBACKS):
            continue
        horse_full = _column_value(cells, column_indexes, '馬名')
        horse_clean = horse_full.split('(')[0].strip()
        results.append({
            '日期': date_str,
            '場次': race_no,
            '路程': distance,
            '場地狀況': going,
            '名次': _column_value(cells, column_indexes, '名次'),
            '馬號': _column_value(cells, column_indexes, '馬號'),
            '馬名': horse_full,
            '騎師': _column_value(cells, column_indexes, '騎師'),
            '練馬師': _column_value(cells, column_indexes, '練馬師'),
            '實際負磅': _column_value(cells, column_indexes, '實際負磅'),
            '排位體重': _column_value(cells, column_indexes, '排位體重'),
            '檔位': _column_value(cells, column_indexes, '檔位'),
            '完成時間': _column_value(cells, column_indexes, '完成時間'),
            '賽後馬匹狀況': incidents.get(horse_clean, ''),
            '跑道': track,
            '賽事班次': race_class,
            '頭馬距離': _column_value(cells, column_indexes, '頭馬距離'),
            '沿途走位': _column_value(cells, column_indexes, '沿途走位'),
            '獨贏賠率': _column_value(cells, column_indexes, '獨贏賠率'),
        })
    return results