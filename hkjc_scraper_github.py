import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import csv
import re
import os
import time
from datetime import datetime, timedelta

async def get_latest_race_date(page):
    """從馬會首頁獲取最近一次有賽事的日期"""
    url = "https://racing.hkjc.com/zh-hk/local/information/localresults"
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector("#selectId")
    
    # 獲取下拉選單中的第一個日期（通常是最近一次賽事）
    content = await page.content()
    soup = BeautifulSoup(content, 'html.parser')
    select = soup.find('select', id='selectId')
    if select and select.find('option'):
        # 格式通常是 DD/MM/YYYY
        date_text = select.find('option').get_text(strip=True)
        # 轉換為 YYYY/MM/DD
        d, m, y = date_text.split('/')
        return f"{y}/{m}/{d}"
    return None

async def scrape_hkjc_results(date_str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        base_url = f"https://racing.hkjc.com/zh-hk/local/information/localresults?date={date_str}"
        print(f"正在訪問日期: {date_str}")
        
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector(".js_racecard", timeout=30000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            race_links = soup.select('.js_racecard a[href*="RaceNo="]')
            race_nos = sorted(list(set([re.search(r'RaceNo=(\d+)', a['href'], re.I).group(1) for a in race_links])), key=int)
            
            if not race_nos: race_nos = ['1']
            print(f"偵測到場次: {race_nos}")
            
            all_data = []
            for race_no in race_nos:
                print(f"正在抓取第 {race_no} 場...")
                selector = f'.js_racecard a[href*="RaceNo={race_no}"]'
                if race_no != '1':
                    await page.click(selector)
                    await page.wait_for_function(f"() => document.body.innerText.includes('第 {race_no} 場')", timeout=15000)
                    await asyncio.sleep(2)

                race_soup = BeautifulSoup(await page.content(), 'html.parser')
                
                # 提取邏輯
                distance = ""
                dist_match = re.search(r'(\d+米)', race_soup.get_text())
                if dist_match: distance = dist_match.group(1)

                going = ""
                going_label = race_soup.find(string=re.compile(r'場地狀況'))
                if going_label:
                    going_td = going_label.find_parent('td')
                    if going_td:
                        next_td = going_td.find_next_sibling('td')
                        if next_td: going = next_td.get_text(strip=True)

                incidents = {}
                incident_table = None
                for t in race_soup.find_all('table'):
                    if '競賽事件' in t.get_text():
                        incident_table = t
                        break
                if incident_table:
                    for row in incident_table.select('tr')[1:]:
                        cols = row.select('td')
                        if len(cols) >= 4:
                            horse = cols[2].get_text(strip=True).split('(')[0].strip()
                            incidents[horse] = cols[3].get_text(strip=True)

                result_table = race_soup.find('table', class_=re.compile(r'draggable'))
                if result_table:
                    for row in result_table.select('tr')[1:]:
                        cols = row.select('td')
                        if len(cols) >= 12:
                            horse_full = cols[2].get_text(strip=True)
                            horse_clean = horse_full.split('(')[0].strip()
                            all_data.append({
                                "日期": date_str,
                                "場次": race_no,
                                "路程": distance,
                                "場地狀況": going,
                                "名次": cols[0].get_text(strip=True),
                                "馬號": cols[1].get_text(strip=True),
                                "馬名": horse_full,
                                "騎師": cols[3].get_text(strip=True),
                                "練馬師": cols[4].get_text(strip=True),
                                "實際負磅": cols[5].get_text(strip=True),
                                "排位體重": cols[6].get_text(strip=True),
                                "檔位": cols[7].get_text(strip=True),
                                "完成時間": cols[10].get_text(strip=True),
                                "賽後馬匹狀況": incidents.get(horse_clean, "")
                            })
            return all_data
        finally:
            await browser.close()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        target_date = await get_latest_race_date(page)
        await browser.close()
        
    if not target_date:
        print("無法獲取最新賽事日期")
        return

    results = await scrape_hkjc_results(target_date)
    if results:
        # 建立 data 資料夾
        os.makedirs('data', exist_ok=True)
        filename = f"data/hkjc_results_{target_date.replace('/', '')}.csv"
        keys = results[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            dict_writer = csv.DictWriter(f, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(results)
        print(f"成功儲存至 {filename}")

if __name__ == "__main__":
    asyncio.run(main())
