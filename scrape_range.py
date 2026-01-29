import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import csv
import re
import os
import time
from datetime import datetime, timedelta

async def get_all_race_dates(page):
    """獲取當前頁面下拉選單中的所有日期"""
    await page.wait_for_selector("#selectId")
    content = await page.content()
    soup = BeautifulSoup(content, 'html.parser')
    select = soup.find('select', id='selectId')
    dates = []
    if select:
        for option in select.find_all('option'):
            date_text = option.get_text(strip=True)
            # 獲取所有日期 (下拉選單不包含場地名稱，稍後在抓取時再判斷是否為香港賽事)
            if re.match(r'\d{2}/\d{2}/\d{4}', date_text):
                try:
                    parts = date_text.split('/')
                    d = parts[0]
                    m = parts[1]
                    y = parts[2].split(' ')[0]
                    dates.append(f"{y}/{m}/{d}")
                except:
                    continue
    return dates

async def scrape_hkjc_results(page, date_str):
    """抓取特定日期的所有場次結果"""
    # 轉換日期格式以便在下拉選單中選擇 (YYYY/MM/DD -> DD/MM/YYYY)
    try:
        parts = date_str.replace('-', '/').split('/')
        y, m, d = parts
        dropdown_date = f"{d}/{m}/{y}"
        formatted_date_str = f"{y}/{m}/{d}"
    except Exception:
        dropdown_date = date_str
        formatted_date_str = date_str

    base_url = f"https://racing.hkjc.com/zh-hk/local/information/localresults?racedate={formatted_date_str}"
    print(f"正在訪問日期: {formatted_date_str}")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            
            # 確保選擇了正確的日期並點擊搜尋
            try:
                await page.wait_for_selector("#selectId", timeout=10000)
                current_val = await page.eval_on_selector("#selectId", "el => el.value")
                
                if current_val != dropdown_date:
                    print(f"目前頁面日期為 {current_val}，正在切換至 {dropdown_date} 並搜尋...")
                    await page.select_option("#selectId", value=dropdown_date)
                    await page.click("#submitBtn")
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"跳過日期選擇步驟: {e}")

            # 檢查是否有賽事數據
            try:
                await page.wait_for_selector(".js_racecard", timeout=15000)
            except:
                print(f"日期 {formatted_date_str} 沒有賽事數據或加載超時 (嘗試 {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    continue
                return []
            
            content = await page.content()
            # 檢查是否為香港賽事 (場地是否包含沙田或跑馬地)
            if not any(v in content for v in ["沙田", "跑馬地"]):
                print(f"日期 {formatted_date_str} 偵測為非香港賽事，跳過。")
                return []

            soup = BeautifulSoup(content, 'html.parser')
            race_links = soup.select('.js_racecard a[href*="RaceNo="]')
            race_nos = sorted(list(set([re.search(r'RaceNo=(\d+)', a['href'], re.I).group(1) for a in race_links])), key=int)
            
            if '1' not in race_nos:
                race_nos.insert(0, '1')
            
            print(f"偵測到場次: {race_nos}")
            
            all_data = []
            for race_no in race_nos:
                print(f"正在抓取第 {race_no} 場...")
                if race_no != '1':
                    selector = f'.js_racecard a[href*="RaceNo={race_no}"]'
                    try:
                        await page.click(selector)
                        await page.wait_for_function(f"() => document.body.innerText.includes('第 {race_no} 場')", timeout=10000)
                        await asyncio.sleep(1)
                    except Exception as e:
                        print(f"點擊第 {race_no} 場失敗: {e}")
                        continue

                race_soup = BeautifulSoup(await page.content(), 'html.parser')
                
                # 提取邏輯 (保持與原腳本一致)
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
        except Exception as e:
            print(f"抓取日期 {date_str} 時發生錯誤 (嘗試 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
                continue
            return []
    return []

async def main():
    start_date = datetime(2025, 1, 1)
    end_date = datetime(2026, 1, 21)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # 1. 獲取當前賽季的所有日期
        print("獲取當前賽季日期...")
        await page.goto("https://racing.hkjc.com/zh-hk/local/information/localresults", wait_until="domcontentloaded")
        all_dates = await get_all_race_dates(page)
        
        # 2. 如果需要更早的日期，嘗試跳轉到上一個賽季
        # 為了保險，我們直接嘗試訪問 2025/01/01 以獲取那個賽季的日期列表
        print("獲取 2024/2025 賽季日期...")
        await page.goto("https://racing.hkjc.com/zh-hk/local/information/localresults?racedate=2025/01/01", wait_until="domcontentloaded")
        all_dates.extend(await get_all_race_dates(page))
        
        # 去重並排序
        all_dates = sorted(list(set(all_dates)), reverse=True)
        
        # 3. 過濾日期範圍
        target_dates = []
        for d_str in all_dates:
            d_obj = datetime.strptime(d_str, "%Y/%m/%d")
            if start_date <= d_obj <= end_date:
                target_dates.append(d_str)
        
        print(f"共找到 {len(target_dates)} 個符合範圍的賽事日期")
        print(f"日期列表: {target_dates}")
        
        # 4. 逐個抓取
        os.makedirs('data', exist_ok=True)
        all_results = []
        
        for date_str in target_dates:
            date_filename = f"data/hkjc_results_{date_str.replace('/', '')}.csv"
            # 檢查是否已經抓取過
            if os.path.exists(date_filename):
                print(f"日期 {date_str} 已存在，跳過...")
                # 讀取現有數據以便最後合併
                with open(date_filename, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    all_results.extend(list(reader))
                continue

            results = await scrape_hkjc_results(page, date_str)
            if results:
                all_results.extend(results)
                # 每個日期存一個臨時檔以防中斷
                date_filename = f"data/hkjc_results_{date_str.replace('/', '')}.csv"
                keys = results[0].keys()
                with open(date_filename, 'w', newline='', encoding='utf-8-sig') as f:
                    dict_writer = csv.DictWriter(f, fieldnames=keys)
                    dict_writer.writeheader()
                    dict_writer.writerows(results)
                print(f"已儲存日期 {date_str} 至 {date_filename}")
            
            # 稍微停頓避免被封
            await asyncio.sleep(2)
        
        # 5. 合併所有結果
        if all_results:
            final_filename = "data/hkjc_results_20250101_20260114.csv"
            keys = all_results[0].keys()
            with open(final_filename, 'w', newline='', encoding='utf-8-sig') as f:
                dict_writer = csv.DictWriter(f, fieldnames=keys)
                dict_writer.writeheader()
                dict_writer.writerows(all_results)
            print(f"成功完成！所有數據已儲存至 {final_filename}")
        else:
            print("未找到任何數據")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
