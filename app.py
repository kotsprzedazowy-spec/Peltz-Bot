import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

TELEGRAM_TOKEN = "8866928843:AAE31hNDFteGCriPVtEJOYr2gezfvRoenKg"
CHAT_ID = "6306627189"

DAYS_BACK = 5
MAX_FORM4_TO_PARSE = 3000
REQUEST_DELAY = 0.15

MIN_MARKET_CAP = 20_000_000
MAX_MARKET_CAP = 5_000_000_000
MIN_SCORE = 8

STATE_FILE = Path("seen_accessions.json")

HEADERS = {
    "User-Agent": "Peltz Bot kontakt@example.com"
}


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=20)
    except Exception as e:
        print("Telegram error:", e)


# TEST — usuń albo zakomentuj po sprawdzeniu, czy GitHub Actions odpala bota co kilka minut
send_telegram(
    f"🧪 TEST: Peltz Bot uruchomiony\nUTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
)


def safe_get(url):
    time.sleep(REQUEST_DELAY)
    try:
        return requests.get(url, headers=HEADERS, timeout=25)
    except Exception as e:
        print("Request error:", e)
        return None


def load_seen():
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except:
        return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(list(seen)), indent=2))


def clean_tag(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def child_by_tag(element, tag_name):
    for child in element:
        if clean_tag(child.tag) == tag_name:
            return child
    return None


def nested_text(element, path):
    current = element
    for tag in path:
        current = child_by_tag(current, tag)
        if current is None:
            return None
    return current.text.strip() if current.text else None


def find_text(element, tag_name):
    for el in element.iter():
        if clean_tag(el.tag) == tag_name and el.text:
            return el.text.strip()
    return None


def find_children(element, tag_name):
    return [el for el in element.iter() if clean_tag(el.tag) == tag_name]


def to_float(value):
    try:
        return float(value)
    except:
        return None


def fmt_money(value):
    if value is None:
        return "brak danych"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} mld USD"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} mln USD"
    if value >= 1_000:
        return f"{value / 1_000:.1f} tys. USD"
    return f"{value:.0f} USD"


def add_months(date_str, months=6):
    try:
        year, month, day = map(int, date_str.split("-"))
        month += months

        while month > 12:
            month -= 12
            year += 1

        if day > 28:
            day = 28

        return f"{year:04d}-{month:02d}-{day:02d}"
    except:
        return "brak danych"


def get_market_cap(ticker):
    if not ticker:
        return None

    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"

    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        result = data["quoteResponse"]["result"]
        if not result:
            return None
        return result[0].get("marketCap")
    except:
        return None


def get_master_index(date):
    year = date.year
    quarter = (date.month - 1) // 3 + 1
    date_txt = date.strftime("%Y%m%d")

    url = f"https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{date_txt}.idx"
    r = safe_get(url)

    if not r or r.status_code != 200:
        return []

    filings = []

    for line in r.text.splitlines():
        if "|" not in line:
            continue

        parts = line.split("|")

        if len(parts) != 5 or parts[0] == "CIK":
            continue

        cik, company_name, form_type, filed_date, path = parts

        if form_type in ["4", "4/A"]:
            accession = path.split("/")[-1].replace(".txt", "")

            filings.append({
                "cik": str(int(cik)),
                "company_name": company_name,
                "form_type": form_type,
                "filed_date": filed_date,
                "path": path,
                "accession": accession
            })

    return filings


def get_xml_url(path):
    accession = path.split("/")[-1].replace(".txt", "")
    accession_clean = accession.replace("-", "")
    folder = "/".join(path.split("/")[:-1])

    index_url = f"https://www.sec.gov/Archives/{folder}/{accession_clean}/index.json"
    r = safe_get(index_url)

    if not r or r.status_code != 200:
        return None

    try:
        data = r.json()
    except:
        return None

    xml_urls = []

    for item in data["directory"]["item"]:
        name = item["name"]
        if name.lower().endswith(".xml"):
            xml_urls.append(f"https://www.sec.gov/Archives/{folder}/{accession_clean}/{name}")

    for xml_url in xml_urls:
        r_xml = safe_get(xml_url)

        if r_xml and r_xml.status_code == 200 and "ownershipDocument" in r_xml.text[:1000]:
            return xml_url

    return None


def get_role(root):
    role = []

    if find_text(root, "isDirector") == "1":
        role.append("Director")

    if find_text(root, "isOfficer") == "1":
        role.append(find_text(root, "officerTitle") or "Officer")

    if find_text(root, "isTenPercentOwner") == "1":
        role.append("10% Owner")

    return ", ".join(role) if role else "Unknown"


def score_buy(buy):
    value = buy["value"]
    role = buy["role"]
    market_cap = buy["market_cap"]
    shares = buy["shares"]
    shares_after = buy["shares_after"]

    score = 0
    reasons = []

    if value >= 10_000_000:
        score += 5
        reasons.append("zakup > 10M USD")
    elif value >= 5_000_000:
        score += 4
        reasons.append("zakup > 5M USD")
    elif value >= 1_000_000:
        score += 3
        reasons.append("zakup > 1M USD")
    elif value >= 500_000:
        score += 2
        reasons.append("zakup > 500k USD")
    elif value >= 100_000:
        score += 1
        reasons.append("zakup > 100k USD")

    role_lower = role.lower()

    if "chief executive" in role_lower or "ceo" in role_lower:
        score += 3
        reasons.append("kupuje CEO")
    elif "chief financial" in role_lower or "cfo" in role_lower:
        score += 3
        reasons.append("kupuje CFO")
    elif "chair" in role_lower:
        score += 3
        reasons.append("kupuje Chairman")
    elif "10% owner" in role_lower:
        score += 3
        reasons.append("kupuje 10% Owner")
    elif "director" in role_lower:
        score += 1
        reasons.append("kupuje Director")

    purchase_to_market_cap = None

    if market_cap and market_cap > 0:
        purchase_to_market_cap = value / market_cap

        if purchase_to_market_cap >= 0.02:
            score += 6
            reasons.append("zakup > 2% market cap")
        elif purchase_to_market_cap >= 0.01:
            score += 5
            reasons.append("zakup > 1% market cap")
        elif purchase_to_market_cap >= 0.005:
            score += 4
            reasons.append("zakup > 0.5% market cap")
        elif purchase_to_market_cap >= 0.0025:
            score += 3
            reasons.append("zakup > 0.25% market cap")
        elif purchase_to_market_cap >= 0.001:
            score += 2
            reasons.append("zakup > 0.1% market cap")

    increase_pct = None

    if shares_after is not None and shares_after > shares:
        before = shares_after - shares

        if before > 0:
            increase_pct = shares / before

            if increase_pct >= 0.50:
                score += 4
                reasons.append("pozycja insidera wzrosła > 50%")
            elif increase_pct >= 0.25:
                score += 3
                reasons.append("pozycja insidera wzrosła > 25%")
            elif increase_pct >= 0.10:
                score += 2
                reasons.append("pozycja insidera wzrosła > 10%")
            elif increase_pct >= 0.05:
                score += 1
                reasons.append("pozycja insidera wzrosła > 5%")

    buy["score"] = score
    buy["reasons"] = reasons
    buy["purchase_to_market_cap"] = purchase_to_market_cap
    buy["increase_pct"] = increase_pct

    return buy


def get_tier(buy):
    market_cap = buy["market_cap"]
    value = buy["value"]
    pct = buy["purchase_to_market_cap"]
    role = buy["role"].lower()
    increase_pct = buy["increase_pct"]

    is_key_insider = (
        "ceo" in role
        or "chief executive" in role
        or "chair" in role
        or "10% owner" in role
        or "founder" in role
    )

    if market_cap is None:
        return "NO MARKET CAP"

    if market_cap < MIN_MARKET_CAP:
        return "TOO SMALL"

    if 20_000_000 <= market_cap < 50_000_000:
        if (
            value >= 500_000
            or (pct and pct >= 0.01)
            or (increase_pct and increase_pct >= 0.25)
            or is_key_insider
        ):
            return "MICROCAP SPECIAL"
        return "IGNORE MICROCAP"

    if 50_000_000 <= market_cap <= 500_000_000:
        if value >= 500_000 or (pct and pct >= 0.005):
            return "TIER 1 PELTZ ALERT"

    if 500_000_000 < market_cap <= 5_000_000_000:
        if value >= 1_000_000 or (pct and pct >= 0.0025):
            return "TIER 2 INSTITUTIONAL"

    if 5_000_000_000 < market_cap <= 100_000_000_000:
        if value >= 5_000_000 or (pct and pct >= 0.001):
            return "TIER 3 LARGE CAP"

    if market_cap > 100_000_000_000:
        if value >= 20_000_000 and is_key_insider:
            return "TIER 3 BLUE CHIP"
        return "IGNORE BLUE CHIP"

    return "IGNORE"


def parse_form4(xml_url):
    r = safe_get(xml_url)

    if not r or r.status_code != 200:
        return []

    try:
        root = ET.fromstring(r.content)
    except:
        return []

    ticker = find_text(root, "issuerTradingSymbol")
    company = find_text(root, "issuerName")
    owner = find_text(root, "rptOwnerName")
    role = get_role(root)

    buys = []

    for txn in find_children(root, "nonDerivativeTransaction"):
        code = nested_text(txn, ["transactionCoding", "transactionCode"])
        acquired = nested_text(txn, ["transactionAmounts", "transactionAcquiredDisposedCode", "value"])

        if code != "P" or acquired != "A":
            continue

        shares = to_float(nested_text(txn, ["transactionAmounts", "transactionShares", "value"]))
        price = to_float(nested_text(txn, ["transactionAmounts", "transactionPricePerShare", "value"]))
        date = nested_text(txn, ["transactionDate", "value"])
        shares_after = to_float(nested_text(txn, ["postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"]))

        if shares is None or price is None:
            continue

        value = shares * price

        buys.append({
            "ticker": ticker,
            "company": company,
            "owner": owner,
            "role": role,
            "shares": shares,
            "price": price,
            "value": value,
            "shares_after": shares_after,
            "date": date,
            "xml_url": xml_url,
            "market_cap": None,
            "score": 0,
            "reasons": [],
            "purchase_to_market_cap": None,
            "increase_pct": None,
            "tier": "UNKNOWN"
        })

    return buys


seen = load_seen()
new_seen = set(seen)

checked = 0
all_buys = []
market_cap_cache = {}

for i in range(DAYS_BACK):
    date = datetime.now() - timedelta(days=i)
    filings = get_master_index(date)

    for filing in filings:
        if checked >= MAX_FORM4_TO_PARSE:
            break

        accession = filing["accession"]

        if accession in seen:
            continue

        checked += 1
        print(f"Sprawdzam {checked}: {filing['company_name']}")

        xml_url = get_xml_url(filing["path"])

        if xml_url:
            buys = parse_form4(xml_url)

            for buy in buys:
                ticker = buy["ticker"]

                if ticker not in market_cap_cache:
                    market_cap_cache[ticker] = get_market_cap(ticker)

                buy["market_cap"] = market_cap_cache[ticker]
                buy["accession"] = accession

                buy = score_buy(buy)
                buy["tier"] = get_tier(buy)

                all_buys.append(buy)

        new_seen.add(accession)

    if checked >= MAX_FORM4_TO_PARSE:
        break


by_ticker = defaultdict(list)

for buy in all_buys:
    if buy["ticker"]:
        by_ticker[buy["ticker"]].append(buy)

for ticker, buys in by_ticker.items():
    unique_owners = set(b["owner"] for b in buys)

    if len(unique_owners) >= 3:
        for b in buys:
            b["score"] += 4
            b["reasons"].append("cluster buying: 3+ insiderów")
    elif len(unique_owners) >= 2:
        for b in buys:
            b["score"] += 2
            b["reasons"].append("cluster buying: 2 insiderów")


qualified_buys = []

for b in all_buys:
    if b["score"] < MIN_SCORE:
        continue

    if b["tier"] in ["IGNORE", "TOO SMALL", "IGNORE MICROCAP", "IGNORE BLUE CHIP"]:
        continue

    if b["tier"] == "NO MARKET CAP":
        if b["value"] >= 1_000_000:
            b["tier"] = "NO MARKET CAP BUT LARGE INSIDER BUY"
            b["reasons"].append("brak market cap, ale zakup > 1M USD")
            qualified_buys.append(b)
        continue

    qualified_buys.append(b)


qualified_buys = sorted(qualified_buys, key=lambda x: x["score"], reverse=True)


for alert in qualified_buys:
    six_month_date = add_months(alert["date"], 6)

    reasons_text = "\n".join([f"- {r}" for r in alert["reasons"]])

    pct_mc = "brak danych"
    if alert["purchase_to_market_cap"] is not None:
        pct_mc = f"{alert['purchase_to_market_cap'] * 100:.3f}%"

    increase_text = "brak danych"
    if alert["increase_pct"] is not None:
        increase_text = f"{alert['increase_pct'] * 100:.2f}%"

    msg = f"""
🚨 {alert['tier']}

Spółka: {alert['company']}
Ticker: {alert['ticker']}

Insider:
{alert['owner']}

Rola:
{alert['role']}

Market cap:
{fmt_money(alert['market_cap'])}

Zakup:
{fmt_money(alert['value'])}

Zakup / market cap:
{pct_mc}

Akcji:
{alert['shares']:,.0f}

Cena:
{alert['price']:.2f} USD

Wzrost pozycji insidera:
{increase_text}

Score:
{alert['score']}/10+

Dlaczego alert:
{reasons_text}

Data zakupu:
{alert['date']}

Koniec 6-miesięcznego okna:
{six_month_date}

Sprzedaż akcji:
Może technicznie sprzedać wcześniej, ale zysk ze sprzedaży przed {six_month_date} może podlegać zasadzie short-swing profit rule.

SEC:
{alert['xml_url']}
"""
    send_telegram(msg)


summary = f"""
✅ Skan zakończony.

Nowych Form 4 sprawdzonych:
{checked}

Wszystkich zakupów P:
{len(all_buys)}

Alertów jakościowych:
{len(qualified_buys)}
"""

if all_buys:
    summary += "\nNajwiększe zakupy P:\n"

    for buy in sorted(all_buys, key=lambda x: x["value"], reverse=True)[:10]:
        six_month_date = add_months(buy["date"], 6)

        pct_mc = "?"
        if buy["purchase_to_market_cap"] is not None:
            pct_mc = f"{buy['purchase_to_market_cap'] * 100:.3f}%"

        summary += f"""
{buy['ticker']} | {buy['owner']}
Zakup: {fmt_money(buy['value'])}
Market cap: {fmt_money(buy['market_cap'])}
Zakup/MC: {pct_mc}
Tier: {buy['tier']}
Score: {buy['score']}
Koniec 6M okna: {six_month_date}
"""

if len(qualified_buys) > 0:
    send_telegram(summary)

save_seen(new_seen)
print(summary)