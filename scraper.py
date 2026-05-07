"""
Çanakkale sayfası için dış veri toplayıcı.
Bağımlılıklar: requests, beautifulsoup4
Çıktılar:
- data/links.json
- data/yemek.json
"""

import json
import os
import argparse
from calendar import monthrange
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

BUS_URL = "https://ulasim.canakkale.bel.tr/rehber/hatlar-otobus-saatleri/"
OSEM_URL = "https://yemek.comu.edu.tr/"
KYK_URL = "https://yurtmenu.net/canakkale"

CATEGORY_KEYWORDS = {
    "haftaici": ["hafta i", "haftai", "weekday"],
    "haftasonu": ["hafta s", "haftason", "weekend"],
    "arefe": ["arefe"],
    "bayram": ["bayram"],
}

TR_MONTHS = [
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
]

TR_WEEKDAYS = [
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
]


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def categorize(text: str) -> str:
    lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lower:
                return category
    return "diger"


def write_json_if_changed(path: str, payload: dict) -> bool:
    old_payload = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as file:
            old_payload = json.load(file)

    if payload == old_payload:
        return False

    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return True


def format_tr_date(date_value: datetime) -> str:
    weekday = TR_WEEKDAYS[date_value.weekday()]
    month = TR_MONTHS[date_value.month - 1]
    return f"{date_value.day} {month} {date_value.year}, {weekday}"


def parse_tr_menu_date(date_text: str) -> str:
    """`Pazartesi, 13 Nisan 2026` formatını ISO tarihe çevirir."""
    cleaned = normalize_text(date_text).replace(",", " ")
    parts = cleaned.split()
    if len(parts) < 4:
        return ""

    # Örn: Pazartesi 13 Nisan 2026
    day_part = parts[1]
    month_part = parts[2]
    year_part = parts[3]

    if not day_part.isdigit() or not year_part.isdigit():
        return ""

    month_index = -1
    for idx, month_name in enumerate(TR_MONTHS, start=1):
        if month_name.lower() == month_part.lower():
            month_index = idx
            break

    if month_index == -1:
        return ""

    return f"{int(year_part):04d}-{month_index:02d}-{int(day_part):02d}"


def current_month_dates() -> list[str]:
    now = datetime.now(ISTANBUL_TZ)
    year = now.year
    month = now.month
    last_day = monthrange(year, month)[1]
    return [f"{year:04d}-{month:02d}-{day:02d}" for day in range(1, last_day + 1)]


def extract_balanced_block(text: str, marker: str, open_char: str = "{", close_char: str = "}") -> str:
    start = text.find(marker)
    if start == -1:
        raise ValueError(f"Marker bulunamadı: {marker}")

    open_index = text.find(open_char, start)
    if open_index == -1:
        raise ValueError(f"Açılış karakteri bulunamadı: {marker}")

    depth = 0
    string_delimiter = None
    escape_next = False

    for index in range(open_index, len(text)):
        char = text[index]

        if string_delimiter is not None:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == string_delimiter:
                string_delimiter = None
            continue

        if char in ('"', "'"):
            string_delimiter = char
            continue

        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]

    raise ValueError(f"Denge blok çıkarılamadı: {marker}")


def scrape_bus_links() -> dict:
    html = fetch_html(BUS_URL)
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "haftaici": [],
        "haftasonu": [],
        "arefe": [],
        "bayram": [],
        "diger": [],
    }

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if not href.lower().endswith(".pdf"):
            continue

        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://ulasim.canakkale.bel.tr" + href

        link_text = normalize_text(tag.get_text(separator=" ", strip=True))
        if not link_text:
            link_text = os.path.basename(href)

        category = categorize(link_text)
        existing_urls = {item["url"] for item in result[category]}
        if href not in existing_urls:
            result[category].append({"label": link_text, "url": href})

    return result


def scrape_osem_menu() -> dict:
    html = fetch_html(OSEM_URL)
    response_block = extract_balanced_block(html, "let response =")
    response = json.loads(response_block)

    today = datetime.now(ISTANBUL_TZ).date().isoformat()
    by_date = {}

    for entry in response.get("data", []):
        start_date_raw = str(entry.get("startDate", ""))
        if not start_date_raw:
            continue

        try:
            start_dt = datetime.strptime(start_date_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        iso_date = start_dt.date().isoformat()
        items = [normalize_text(item) for item in entry.get("foodName", []) if normalize_text(item)]
        by_date[iso_date] = {
            "date": format_tr_date(start_dt),
            "items": items,
        }

    selected = by_date.get(today)
    if selected is None and by_date:
        selected = by_date[sorted(by_date.keys())[0]]
    if selected is None:
        selected = {"date": "", "items": []}

    return {
        "sourceUrl": OSEM_URL,
        "date": selected.get("date", ""),
        "items": selected.get("items", []),
        "monthly": {
            "yearMonth": today[:7],
            "days": by_date,
        },
    }


def scrape_kyk_menu() -> dict:
    # Check if we already have valid data for this month
    today = datetime.now(ISTANBUL_TZ).date().isoformat()
    current_month = today[:7]
    try:
        if os.path.exists("data/yemek.json"):
            with open("data/yemek.json", "r", encoding="utf-8") as f:
                existing = json.load(f)
                kyk_data = existing.get("kyk", {})
                existing_month = kyk_data.get("monthly", {}).get("yearMonth")
                existing_days = kyk_data.get("monthly", {}).get("days", {})
                
                # Eğer bu aya ait veri varsa ve 15 günden fazlaysa (aylık liste tam çekilmişse)
                if existing_month == current_month and len(existing_days) > 15:
                    print("Bu ayin KYK verisi zaten kayitli, tekrar cekilmiyor.")
                    # Mevcut veriyi sadece güncelleyip dön, network isteği atma
                    selected = existing_days.get(today)
                    if not selected and existing_days:
                        selected = existing_days[sorted(existing_days.keys())[0]]
                    if not selected:
                        selected = {"date": "", "sabah": {"date": "", "items": [], "calories": ""}, "aksam": {"date": "", "items": [], "calories": ""}}
                    
                    # Mevcut veriyi döndürüyoruz
                    kyk_data["date"] = selected.get("date", "")
                    kyk_data["sabah"] = selected.get("sabah", {"date": "", "items": [], "calories": ""})
                    kyk_data["aksam"] = selected.get("aksam", {"date": "", "items": [], "calories": ""})
                    return kyk_data
    except Exception as e:
        print("Mevcut KYK verisi kontrol edilirken hata:", e)

    def read_meal_from_soup(soup: BeautifulSoup, meal_id: str) -> dict:
        meal_card = soup.select_one(f"article#{meal_id}-card")
        meal_menu = soup.select_one(f"#{meal_id}-menu")

        items = []
        if meal_menu:
            items = [
                normalize_text(item.get_text(" ", strip=True))
                for item in meal_menu.select(".menu-item-name")
            ]
            items = [item for item in items if item]

        calories = ""
        if meal_card:
            calorie_tag = meal_card.select_one(".menu-card-meta")
            if calorie_tag:
                calories = normalize_text(calorie_tag.get_text(" ", strip=True))

        return {
            "items": items,
            "calories": calories,
        }

    monthly_days = {}
    today = datetime.now(ISTANBUL_TZ).date().isoformat()
    
    fallback_urls = [
        KYK_URL,
        "https://kykyemek.com/",
        "https://kykyemekliste.com/"
    ]
    
    # Try fetching from the primary URL first
    # If the response contains "veri yok", we use fallback from kykyemek.com
    
    try:
        html = fetch_html("https://kykyemek.com/")
        soup = BeautifulSoup(html, "html.parser")
        
        # 1. Parse kykyemek.com globally
        # We know breakfast is followed by "Akşam Yemeği" div
        sabah_data = {}
        aksam_data = {}
        
        is_dinner = False
        current_date_text = None
        items = []
        
        # We look through all paragraphs and divs
        for tag in soup.find_all(["p", "div"]):
            text = tag.text.strip()
            if not text: continue
            
            if "Akşam Yemeği" in text:
                is_dinner = True
                current_date_text = None
                items = []
                continue
                
            # Date check
            is_date = False
            for m in TR_MONTHS:
                if m in text and "2026" in text:
                    is_date = True
                    break
                    
            if is_date and tag.name == "p":
                if current_date_text:
                    if is_dinner:
                        aksam_data[current_date_text] = items
                    else:
                        sabah_data[current_date_text] = items
                current_date_text = text
                items = []
            elif tag.name == "p":
                if current_date_text and "kalori" not in text.lower() and len(text) > 2:
                    if text not in items:  # avoid duplicates
                        items.append(text)
                elif current_date_text and "kalori" in text.lower():
                    if is_dinner:
                        aksam_data[current_date_text] = items
                    else:
                        sabah_data[current_date_text] = items
                    current_date_text = None
                    items = []
        
        # Convert fetched data to monthly_days format
        for date_text, sabah_items in sabah_data.items():
            parsed_iso = parse_tr_menu_date(date_text)
            if not parsed_iso: continue
            
            aksam_items = aksam_data.get(date_text, [])
            
            monthly_days[parsed_iso] = {
                "date": date_text,
                "sabah": {
                    "date": date_text,
                    "items": sabah_items,
                    "calories": ""
                },
                "aksam": {
                    "date": date_text,
                    "items": aksam_items,
                    "calories": ""
                }
            }
            
    except Exception as e:
        print("Fallback fetch error:", e)

    # Use primary source yurtmenu.net per day to override if it actually has data
    for iso_date in current_month_dates():
        html = fetch_html(f"{KYK_URL}?date={iso_date}")
        soup = BeautifulSoup(html, "html.parser")

        date_tag = soup.select_one("#menu-date")
        date_text = normalize_text(date_tag.get_text(" ", strip=True)) if date_tag else ""
        parsed_iso = parse_tr_menu_date(date_text) or iso_date

        breakfast = read_meal_from_soup(soup, "breakfast")
        dinner = read_meal_from_soup(soup, "dinner")
        
        # Sadece "Veri yok" boşluğu değilse üzerine yaz
        has_primary_data = False
        if breakfast["items"] and "Veri yok" not in breakfast["items"][0]:
            has_primary_data = True
            
        if has_primary_data:
            monthly_days[parsed_iso] = {
                "date": date_text,
                "sabah": {
                    "date": date_text,
                    "items": breakfast["items"],
                    "calories": breakfast["calories"],
                },
                "aksam": {
                    "date": date_text,
                    "items": dinner["items"],
                    "calories": dinner["calories"],
                },
            }

    selected = monthly_days.get(today)
    if selected is None and monthly_days:
        selected = monthly_days[sorted(monthly_days.keys())[0]]
    if selected is None:
        selected = {
            "date": "",
            "sabah": {"date": "", "items": [], "calories": ""},
            "aksam": {"date": "", "items": [], "calories": ""},
        }

    return {
        "sourceUrl": KYK_URL,
        "date": selected.get("date", ""),
        "sabah": {
            "date": selected.get("sabah", {}).get("date", ""),
            "items": selected.get("sabah", {}).get("items", []),
            "calories": selected.get("sabah", {}).get("calories", ""),
        },
        "aksam": {
            "date": selected.get("aksam", {}).get("date", ""),
            "items": selected.get("aksam", {}).get("items", []),
            "calories": selected.get("aksam", {}).get("calories", ""),
        },
        "monthly": {
            "yearMonth": today[:7],
            "days": monthly_days,
        },
    }


def main(target: str = "all"):
    os.makedirs("data", exist_ok=True)

    links_changed = False
    yemek_changed = False

    if target in ("all", "bus"):
        links_changed = write_json_if_changed(os.path.join("data", "links.json"), scrape_bus_links())

    if target in ("all", "meal"):
        yemek_data = {
            "generatedAt": datetime.now(ISTANBUL_TZ).isoformat(),
            "osem": scrape_osem_menu(),
            "kyk": scrape_kyk_menu(),
        }
        yemek_changed = write_json_if_changed(os.path.join("data", "yemek.json"), yemek_data)

    if not links_changed and not yemek_changed:
        print("Değişiklik yok, dosyalar güncellenmedi.")
        return

    if links_changed:
        print("data/links.json güncellendi.")
    if yemek_changed:
        print("data/yemek.json güncellendi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Çanakkale veri toplayıcı")
    parser.add_argument(
        "--target",
        choices=["all", "bus", "meal"],
        default="all",
        help="Hangi verinin çekileceği",
    )
    args = parser.parse_args()
    main(args.target)
