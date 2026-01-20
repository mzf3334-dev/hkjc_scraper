import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import csv
import re
import os
import time
from datetime import datetime, timedelta, timezone

async def get_latest_race_date(page):
    """從馬會首頁獲取最近一次有賽事的日期"""
    url = "https://racing.hkjc.com/zh-hk/local/information/localresults"
    try:
        # 使用 wait_until="load" 確保頁面完全載入
        await page.goto(url, wait_until="load", timeout=60000)
        # 使用 state="attached" 因為 option 在下拉選單未展開時可能被視為不可見
        await page.wait_for_selector("#selectId option", state="attached", timeout=30000)
        
        # 獲取下拉選單中的第一個日期（通常是最近一次賽事）
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        select = soup.find('select', id='selectId')
        
        if select:
            options = select.find_all('option')
            
            # 獲取香港當前日期 (UTC+8)
            # GitHub Actions 預設是 UTC，所以加 8 小時以符合香港時間
            now_hk = datetime.now(timezone.utc) + timedelta(hours=8)
            today_str = now_hk.strftime('%Y/%m/%d')
            print(f"基準日期 (香港時間): {today_str}")

            for option in options:
                date_text = option.get_text(strip=True)
                # 檢查是否符合 DD/MM/YYYY 格式
                if re.match(r'\d{2}/\d{2}/\d{4}', date_text):
                    d, m, y = date_text.split('/')
                    date_val = f"{y}/{m}/{d}"
                    # 只接受今天或之前的日期，避免抓取到尚未有結果的未來賽事
                    if date_val <= today_str:
                        return date_val
    except Exception as e:
        print(f"獲取最新日期時出錯: {e}")
    return None

async def scrape_hkjc_results(date_str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # 轉換日期格式以便在下拉選單中選擇 (YYYY/MM/DD -> DD/MM/YYYY)
        try:
            parts = date_str.replace('-', '/').split('/')
            y, m, d = parts
            dropdown_date = f"{d}/{m}/{y}"
            formatted_date_str = f"{y}/{m}/{d}"
        except Exception:
            dropdown_date = date_str
            formatted_date_str = date_str

        # 使用 racedate 而非 date 參數
        base_url = f"https://racing.hkjc.com/zh-hk/local/information/localresults?racedate={formatted_date_str}"
        print(f"正在訪問日期: {formatted_date_str}")
        
        try:
            # 使用 wait_until="load" 確保頁面完全載入
            await page.goto(base_url, wait_until="load", timeout=60000)
            
            # 確保選擇了正確的日期並點擊搜尋
            try:
                await page.wait_for_selector("#selectId", timeout=15000)
                current_val = await page.eval_on_selector("#selectId", "el => el.value")
                
                if current_val != dropdown_date:
                    print(f"目前頁面日期為 {current_val}，正在切換至 {dropdown_date} 並搜尋...")
                    await page.select_option("#selectId", value=dropdown_date)
                    await page.click("#submitBtn")
                    # 等待導航或載入
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(3) # 給予額外的時間讓 AJAX 內容載入
            except Exception as e:
                print(f"跳過日期選擇步驟: {e}")

            await page.wait_for_selector(".js_racecard", timeout=30000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            race_links = soup.select('.js_racecard a[href*="RaceNo="]')
            race_nos = sorted(list(set([re.search(r'RaceNo=(\d+)', a['href'], re.I).group(1) for a in race_links])), key=int)
            
            # 確保包含第一場
            if '1' not in race_nos:
                race_nos.insert(0, '1')
            
            print(f"偵測到場次: {race_nos}")
            
            all_data = []
            for race_no in race_nos:
                print(f"正在抓取第 {race_no} 場...")
                selector = f'.js_racecard a[href*="RaceNo={race_no}"]'
                if race_no != '1':
                    try:
                        await page.click(selector)
                        # 等待內容更新
                        await page.wait_for_function(f"() => document.body.innerText.includes('第 {race_no} 場')", timeout=15000)
                        await asyncio.sleep(1)
                    except Exception as e:
                        print(f"點擊第 {race_no} 場失敗: {e}")
                        continue

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
    # 1. 獲取最新賽事日期
    latest_date = None
    print("正在獲取最新賽事日期...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        try:
            latest_date = await get_latest_race_date(page)
        except Exception as e:
            print(f"獲取最新日期時發生錯誤: {e}")
        finally:
            await browser.close()

    if not latest_date:
        print("未能獲取最新日期，程式終止。")
        return

    print(f"偵測到最新賽事日期: {latest_date}")
    
    # 2. 處理該日期
    target_date = latest_date
    print(f"\n--- 開始處理日期: {target_date} ---")
    
    # 建立 data 資料夾
    os.makedirs('data', exist_ok=True)
    filename = f"data/hkjc_results_{target_date.replace('/', '')}.csv"
    
    # 如果檔案已存在，則跳過（避免 GitHub Actions 重複執行浪費資源）
    if os.path.exists(filename):
        print(f"檔案 {filename} 已存在，跳過抓取。")
        return

    results = await scrape_hkjc_results(target_date)
    
    if results:
        keys = results[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            dict_writer = csv.DictWriter(f, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(results)
        print(f"成功儲存至 {filename}")
    else:
        print(f"日期 {target_date} 無法獲取資料")

if __name__ == "__main__":
    asyncio.run(main())
