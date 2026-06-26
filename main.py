import requests
import time
import re
import unicodedata
from datetime import datetime
import gspread
from decouple import config

# ==========================================
# 1. НАЛАШТУВАННЯ ТА КОНСТАНТИ
# ==========================================
AUTORIA_API_KEY = config('RIA_TOKEN')  # API ключ
MAX_CAR_PRICE_CZK = 50000

# Фіксовані витрати (в євро)
LOGISTICS_EUR = 700
CERTIFICATION_EUR = 100
PENSION_FUND_EUR = 150

# Курс валют
CZK_TO_EUR = 0.039  # 1 крона = ~0.039 евро
USD_TO_EUR = 0.92  # 1 доллар = ~0.92 евро

# Словники маппінга для Auto.RIA
MARK_MAPPER = {
    "vw": "volkswagen",
    "mercedes": "mercedes-benz",
    "alfa": "alfa romeo",
    "ssang yong": "ssangyong",
    "land-rover": "land rover"
}

MODEL_MAPPER = {
    "volkswagen": {
        "passat variant": "passat",
        "golf variant": "golf",
        "transporter / caravelle": "transporter"
    },
    "skoda": {
        "octavia combi": "octavia",
        "superb combi": "superb",
        "fabia combi": "fabia"
    }
}


# ==========================================
# 2. КЛІЄНТ AUTO.RIA
# ==========================================
class AutoRiaClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://developers.ria.com/auto"
        self._marks_cache = []
        self._models_cache = {}

    def normalize_name(self, name: str) -> str:
        # Очищення рядків від чеських символів (Škoda -> skoda)
        if not name: return ""
        name = name.lower().strip()
        name = ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
        return name

    def get_mark_id(self, mark_name):
        if not self._marks_cache:
            url = f"{self.base_url}/categories/1/marks?api_key={self.api_key}"
            response = requests.get(url).json()
            if isinstance(response, dict) and "error" in response:
                print(f"[-] Помилка API RIA (marks): {response.get('error')}")
                return None
            self._marks_cache = response

        for mark in self._marks_cache:
            if mark['name'].lower() == mark_name.lower():
                return mark['value']
        return None

    def get_model_id(self, mark_id, model_name):
        if mark_id not in self._models_cache:
            url = f"{self.base_url}/categories/1/marks/{mark_id}/models?api_key={self.api_key}"
            response = requests.get(url).json()
            if isinstance(response, dict) and "error" in response:
                return None
            self._models_cache[mark_id] = response

        for model in self._models_cache[mark_id]:
            if model['name'].lower() == model_name.lower():
                return model['value']
        return None
#####
# оновлений     get_average_price
####
    def get_average_price(self, raw_mark_name, raw_model_name, year):
        clean_mark = self.normalize_name(raw_mark_name)
        mapped_mark = MARK_MAPPER.get(clean_mark, clean_mark)

        mark_id = self.get_mark_id(mapped_mark)
        if not mark_id:
            print(f"[!] DEBUG RIA: Марку '{mapped_mark}' (оригінал: {raw_mark_name}) не знайдено.")
            return None

        clean_model = self.normalize_name(raw_model_name)
        mapped_model = clean_model
        if mapped_mark in MODEL_MAPPER:
            mapped_model = MODEL_MAPPER[mapped_mark].get(clean_model, clean_model)

        model_id = self.get_model_id(mark_id, mapped_model)
        if not model_id:
            print(
                f"[!] DEBUG RIA: Модель '{mapped_model}' (оригінал: {raw_model_name}) для марки {mapped_mark} не знайдено.")
            return None

        url = f"{self.base_url}/average_price?api_key={self.api_key}"
        params = {"marka_id": mark_id, "model_id": model_id, "yers": year}

        response = requests.get(url, params=params).json()

        # Перевіряємо чи взагалі є статистика
        if "error" in response or response.get("total", 0) == 0:
            print(f"[!] DEBUG RIA: Немає статистики цін для {mapped_mark} {mapped_model} {year} року. Total = 0.")
            return None

        return {
            "real_market_price": response.get("interQuartileMean", response.get("arithmeticMean"))
        }
#####
    # def get_average_price(self, raw_mark_name, raw_model_name, year):
    #     clean_mark = self.normalize_name(raw_mark_name)
    #     mapped_mark = MARK_MAPPER.get(clean_mark, clean_mark)
    #
    #     mark_id = self.get_mark_id(mapped_mark)
    #     if not mark_id: return None
    #
    #     clean_model = self.normalize_name(raw_model_name)
    #     mapped_model = clean_model
    #     if mapped_mark in MODEL_MAPPER:
    #         mapped_model = MODEL_MAPPER[mapped_mark].get(clean_model, clean_model)
    #
    #     model_id = self.get_model_id(mark_id, mapped_model)
    #     if not model_id: return None
    #
    #     url = f"{self.base_url}/average_price?api_key={self.api_key}"
    #     params = {"marka_id": mark_id, "model_id": model_id, "yers": year}
    #
    #     response = requests.get(url, params=params).json()
    #     if "error" in response or response.get("total", 0) == 0:
    #         return None
    #
    #     return {
    #         "real_market_price": response.get("interQuartileMean", response.get("arithmeticMean"))
    #     }


# ==========================================
# 3. ЛОГІКА ТОМОЖНІ І ПАРСІНГА SAUTO
# ==========================================
def calculate_ukraine_customs(price_eur: float, volume_cm3: int, year: int, fuel_type: str) -> dict:
    current_year = datetime.now().year
    fuel_type = fuel_type.lower()

    duty_cost = price_eur * (0.0 if fuel_type == 'electric' else 0.10)

    excise_cost = 0.0
    if fuel_type != 'electric':
        if fuel_type == 'petrol':
            base_rate = 50 if volume_cm3 <= 3000 else 100
        elif fuel_type == 'diesel':
            base_rate = 75 if volume_cm3 <= 3500 else 150
        else:
            base_rate = 50

        age_coef = max(1, min(current_year - year - 1, 15))
        volume_coef = volume_cm3 / 1000.0
        excise_cost = base_rate * volume_coef * age_coef

    vat_cost = 0.0 if fuel_type == 'electric' else (price_eur + duty_cost + excise_cost) * 0.20
    total_customs = duty_cost + excise_cost + vat_cost

    return {"total_customs_eur": total_customs, "final_cost_in_ua_eur": price_eur + total_customs}


def parse_engine_volume(text: str) -> int:
    if not text: return 0
    match = re.search(r'(\d)[.,](\d)', text)
    return int(float(f"{match.group(1)}.{match.group(2)}") * 1000) if match else 0


def map_fuel_type(cz_fuel_name: str) -> str:
    if not cz_fuel_name: return 'petrol'
    cz_fuel_name = cz_fuel_name.lower()
    if 'nafta' in cz_fuel_name: return 'diesel'
    if 'elektro' in cz_fuel_name: return 'electric'
    return 'petrol'


# def fetch_sauto_cars(price_to, limit=20):
#     url = "https://www.sauto.cz/api/v1/items/search"
#     params = {
#         "limit": limit, "offset": 0, "price_to": price_to,
#         "condition_seo": "nove,ojete,predvadeci", "category_id": 838, "operating_lease": "false"
#     }
#     headers = {
#         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#         "Accept": "application/json"
#     }
#
#     parsed_cars = []
#     while True:
#         print(f"[*] Sauto: завантаження сторінки (offset: {params['offset']})...")
#         try:
#             response = requests.get(url, params=params, headers=headers)
#             response.raise_for_status()
#         except requests.exceptions.RequestException:
#             break
#
#         items = response.json().get("results", [])
#         if not items: break
#
#         for item in items:
#             op_date = item.get("in_operation_date")
#             vol_text = item.get("additional_model_name") or item.get("name", "")
#
#             parsed_cars.append({
#                 "id": item.get("id"),
#                 "mark": item.get("manufacturer_cb", {}).get("name", "Unknown"),
#                 "model": item.get("model_cb", {}).get("name", "Unknown"),
#                 "full_name": item.get("name", "Unknown"),
#                 "price_czk": item.get("price", 0),
#                 "year": int(op_date[:4]) if op_date else None,
#                 "volume_cm3": parse_engine_volume(vol_text),
#                 "fuel_type": map_fuel_type(item.get("fuel_cb", {}).get("name", "")),
#                 "link": f"https://www.sauto.cz/detail/{item.get('id')}"
#             })
#
#         params["offset"] += limit
#         time.sleep(1.5)
#         if params["offset"] >= limit * 3: break  # ПРИБЕРИ ЦЕЙ РЯДОК ДЛЯ ПАРСИНГУ ВСІХ СТОРІНОК!
#
#     return parsed_cars

#############################
#TEST
def fetch_sauto_cars(price_to, limit=20):
    url = "https://www.sauto.cz/api/v1/items/search"
    params = {
        "limit": limit, "offset": 0, "price_to": price_to,
        "condition_seo": "nove,ojete,predvadeci", "category_id": 838, "operating_lease": "false"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    parsed_cars = []
    while True:
        print(f"[*] Sauto: завантаження сторінки (offset: {params['offset']})...")
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            break

        items = response.json().get("results", [])
        if not items: break

        for item in items:
            op_date = item.get("in_operation_date")
            vol_text = item.get("additional_model_name") or item.get("name", "")

            # Отримуємо марку та модель для формування правильного URL
            mark_name = item.get("manufacturer_cb", {}).get("name", "Unknown")
            model_name = item.get("model_cb", {}).get("name", "Unknown")

            # Робимо URL-friendly рядки
            mark_url = mark_name.lower().replace(" ", "-")
            model_url = model_name.lower().replace(" ", "-")

            parsed_cars.append({
                "id": item.get("id"),
                "mark": mark_name,
                "model": model_name,
                "full_name": item.get("name", "Unknown"),
                "price_czk": item.get("price", 0),
                "year": int(op_date[:4]) if op_date else None,
                "volume_cm3": parse_engine_volume(vol_text),
                "fuel_type": map_fuel_type(item.get("fuel_cb", {}).get("name", "")),
                # Оновлене, робоче посилання:
                "link": f"https://www.sauto.cz/osobni/detail/{mark_url}/{model_url}/{item.get('id')}"
            })

        params["offset"] += limit
        time.sleep(1.5)
        if params["offset"] >= limit * 1: break  # ПРИБЕРИ ЦЕЙ РЯДОК ДЛЯ ПАРСИНГУ ВСІХ СТОРІНОК!

    return parsed_cars

# ==========================================
# 4. ГОЛОВНИЙ ЦИКЛ
# ==========================================
def main():
    print("=== ЗАПУСК СКРИПТА АРБІТРАЖА АВТО ===")
    ria = AutoRiaClient(api_key=AUTORIA_API_KEY)
    google_sheets_data = []

    cars = fetch_sauto_cars(price_to=MAX_CAR_PRICE_CZK, limit=20)
    print(f"\n[+] Знайдено автомобілів на Sauto: {len(cars)}\n")
    print("=== ПОЧАТОК АНАЛІЗУ ТА ФІЛЬТРАЦІЇ ===")

    # Словник для ведення статистики
    stats = {
        "no_volume_or_year": 0,
        "no_ria_data": 0,
        "success": 0
    }

    for car in cars:
        # Пропускаємо, якщо парсер не знайшов рік або об'єм
        if not car['year'] or car['volume_cm3'] == 0:
            print(
                f"[-] ПРОПУСК (Дані Sauto): {car['mark']} {car['model']} | Причина: Немає року ({car['year']}) або об'єму ({car['volume_cm3']} см3). Повна назва: {car['full_name']}")
            stats["no_volume_or_year"] += 1
            continue

        # Рахуємо розтаможку
        price_eur = car['price_czk'] * CZK_TO_EUR
        customs = calculate_ukraine_customs(price_eur, car['volume_cm3'], car['year'], car['fuel_type'])
        total_cost_eur = customs['final_cost_in_ua_eur'] + LOGISTICS_EUR + CERTIFICATION_EUR + PENSION_FUND_EUR

        # Запитуємо ціну в Україні
        time.sleep(0.5)  # Бережем ліміти API
        ria_data = ria.get_average_price(car['mark'], car['model'], car['year'])

        if not ria_data or not ria_data.get('real_market_price'):
            print(
                f"[-] ПРОПУСК (Auto.RIA): {car['mark']} {car['model']} {car['year']} | Причина: Не знайдено в базі RIA або немає статистики цін. Перевір MARK_MAPPER/MODEL_MAPPER.")
            stats["no_ria_data"] += 1
            continue

        market_price_eur = ria_data['real_market_price'] * USD_TO_EUR
        net_profit_eur = market_price_eur - total_cost_eur
        roi_percent = (net_profit_eur / total_cost_eur) * 100

        # Машина успішно пройшла всі фільтри
        print(f"[+] УСПІХ: {car['mark']} {car['model']} {car['year']} | Профіт: {net_profit_eur:.0f}€")
        stats["success"] += 1

        # Гугл Таблицы
        row = [
            car['mark'], car['model'], car['year'], car['volume_cm3'], car['fuel_type'],
            f"{car['price_czk']} CZK", round(price_eur, 2),
            round(customs['total_customs_eur'], 2), round(total_cost_eur, 2),
            round(market_price_eur, 2), round(net_profit_eur, 2),
            f"{round(roi_percent, 1)}%", car['link']
        ]
        google_sheets_data.append(row)

    print("\n=== ПІДСУМКИ АНАЛІЗУ ===")
    print(f"Всього перевірено: {len(cars)}")
    print(f"Відкинуто через відсутність об'єму/року: {stats['no_volume_or_year']}")
    print(f"Відкинуто через відсутність даних Auto.RIA: {stats['no_ria_data']}")
    print(f"Успішно зібрано для таблиці: {stats['success']}")

    print(f"\n[*] Аналіз завершений. Готово рядків для запису: {len(google_sheets_data)}")

############################



# ==========================================
# 4. ГОЛОВНИЙ ЦИКЛ
# ==========================================
# def main():
#     print("=== ЗАПУСК СКРИПТА АРБІТРАЖА АВТО ===")
#     ria = AutoRiaClient(api_key=AUTORIA_API_KEY)
#     google_sheets_data = []
#
#     cars = fetch_sauto_cars(price_to=MAX_CAR_PRICE_CZK, limit=20)
#     print(f"[+] Знайдено автомобілів на Sauto: {len(cars)}\n")
#
#     for car in cars:
#         # Пропускаємо, якщо парсер не знайшов рік або обєм
#         if not car['year'] or car['volume_cm3'] == 0:
#             continue
#
#         # Рахуємо розтаможку
#         price_eur = car['price_czk'] * CZK_TO_EUR
#         customs = calculate_ukraine_customs(price_eur, car['volume_cm3'], car['year'], car['fuel_type'])
#         total_cost_eur = customs['final_cost_in_ua_eur'] + LOGISTICS_EUR + CERTIFICATION_EUR + PENSION_FUND_EUR
#
#         # Запитуємо ціну в Україні
#         time.sleep(0.5)  # Бережем ліміти API
#         ria_data = ria.get_average_price(car['mark'], car['model'], car['year'])
#
#         if not ria_data or not ria_data.get('real_market_price'):
#             continue
#
#         market_price_eur = ria_data['real_market_price'] * USD_TO_EUR
#         net_profit_eur = market_price_eur - total_cost_eur
#         roi_percent = (net_profit_eur / total_cost_eur) * 100
#
#         # Вивід в консоль тільки прибуткових машин (опціонально)
#         # print(
#         #     f"[{car['mark']} {car['model']} {car['year']}] Чехія: {price_eur:.0f}€ | Ринок UA: {market_price_eur:.0f}€ | Профіт: {net_profit_eur:.0f}€")
#
#         # Гугл Таблицы
#         row = [
#             car['mark'], car['model'], car['year'], car['volume_cm3'], car['fuel_type'],
#             f"{car['price_czk']} CZK", round(price_eur, 2),
#             round(customs['total_customs_eur'], 2), round(total_cost_eur, 2),
#             round(market_price_eur, 2), round(net_profit_eur, 2),
#             f"{round(roi_percent, 1)}%", car['link']
#         ]
#         google_sheets_data.append(row)
#
#     print(f"\n[*] Аналіз завершений. Готово рядків для запису: {len(google_sheets_data)}")


# ==========================================
# 5. ЗАПИС В Google Tabs
# ==========================================
    # 1. Підключення
    try:
        # Робот читає свій файл-пропуск
        gc = gspread.service_account(filename='google_keys.json')
        # Откриваємо таблицю по назві
        sheet = gc.open("AutoParser").sheet1
        print("✅ Успішно підключились до Google Tabs!")
    except Exception as e:
        print(f"❌ Помилка підключення до Google: {e}")
        sheet = None

    # 2. Запис
    if sheet and google_sheets_data:
        try:
            # Пакетная вставка всех знайденних рядків одразу
            sheet.append_rows(google_sheets_data)
            print(f"🚀 Дані успішно вигружені в таблицю! Додано рядків: {len(google_sheets_data)}")
        except Exception as e:
            print(f"❌ Помилка при записі даних: {e}")
    else:
        print("⚠️ Запис невиконаний: або нема підключення, або список даних порожній.")

if __name__ == "__main__":
    main()