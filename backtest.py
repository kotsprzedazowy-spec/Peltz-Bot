import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# Wczytaj alerty
alerts = pd.read_csv("alerts.csv")

results = []

for _, row in alerts.iterrows():
    ticker = row["ticker"]
    alert_date = pd.to_datetime(row["date"])

    try:
        stock = yf.Ticker(ticker)

        hist = stock.history(
            start=(alert_date - timedelta(days=5)).strftime("%Y-%m-%d"),
            end=(alert_date + timedelta(days=40)).strftime("%Y-%m-%d")
        )

        if len(hist) < 5:
            continue

        entry_price = hist.iloc[0]["Close"]

        price_5d = None
        price_30d = None

        if len(hist) > 5:
            price_5d = hist.iloc[min(5, len(hist)-1)]["Close"]

        if len(hist) > 30:
            price_30d = hist.iloc[min(30, len(hist)-1)]["Close"]

        return_5d = None
        return_30d = None

        if price_5d:
            return_5d = (price_5d / entry_price - 1) * 100

        if price_30d:
            return_30d = (price_30d / entry_price - 1) * 100

        results.append({
            "ticker": ticker,
            "date": alert_date.strftime("%Y-%m-%d"),
            "score": row["score"],
            "return_5d": return_5d,
            "return_30d": return_30d
        })

    except Exception as e:
        print(f"Błąd {ticker}: {e}")

pd.DataFrame(results).to_csv("backtest_results.csv", index=False)

print("Gotowe.")