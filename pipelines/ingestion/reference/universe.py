"""Reference universe: real NSE names, real ISINs, AMFI-style cap buckets.

AMFI publishes the official classification half-yearly: top 100 by market cap =
large, 101-250 = mid, 251+ = small. Cap bucket is effective-dated in the real
system; this seed is a point-in-time snapshot for bootstrapping.
"""
from __future__ import annotations

import pandas as pd

# (name, isin, nse_symbol, smartapi_token, cap_bucket, sector, free_float_shares_cr)
UNIVERSE: list[tuple] = [
    ("HDFC Bank Ltd",             "INE040A01034", "HDFCBANK",  "1333",  "large", "Financials",  765.0),
    ("Reliance Industries Ltd",   "INE002A01018", "RELIANCE",  "2885",  "large", "Energy",      676.0),
    ("ICICI Bank Ltd",            "INE090A01021", "ICICIBANK", "4963",  "large", "Financials",  704.0),
    ("Infosys Ltd",               "INE009A01021", "INFY",      "1594",  "large", "IT",          415.0),
    ("Tata Consultancy Svcs Ltd", "INE467B01029", "TCS",       "11536", "large", "IT",           99.0),
    ("Bharti Airtel Ltd",         "INE397D01024", "BHARTIARTL","10604", "large", "Telecom",     238.0),
    ("Larsen & Toubro Ltd",       "INE018A01030", "LT",        "11483", "large", "Industrials", 137.0),
    ("Axis Bank Ltd",             "INE238A01034", "AXISBANK",  "5900",  "large", "Financials",  309.0),
    ("State Bank of India",       "INE062A01020", "SBIN",      "3045",  "large", "Financials",  383.0),
    ("ITC Ltd",                   "INE154A01025", "ITC",       "1660",  "large", "FMCG",       1252.0),
    ("Kotak Mahindra Bank Ltd",   "INE237A01028", "KOTAKBANK", "1922",  "large", "Financials",  148.0),
    ("Hindustan Unilever Ltd",    "INE030A01027", "HINDUNILVR","1394",  "large", "FMCG",        90.0),
    ("Bajaj Finance Ltd",         "INE296A01024", "BAJFINANCE","317",   "large", "Financials",   28.0),
    ("Maruti Suzuki India Ltd",   "INE585B01010", "MARUTI",    "10999", "large", "Auto",         13.0),
    ("Asian Paints Ltd",          "INE021A01026", "ASIANPAINT","236",   "large", "Materials",    46.0),
    ("Titan Company Ltd",         "INE280A01028", "TITAN",     "3506",  "large", "Consumer",     41.0),
    ("Sun Pharmaceutical Ind",    "INE044A01036", "SUNPHARMA", "3351",  "large", "Pharma",      110.0),
    ("UltraTech Cement Ltd",      "INE481G01011", "ULTRACEMCO","11532", "large", "Materials",    12.0),
    ("Nestle India Ltd",          "INE239A01024", "NESTLEIND", "17963", "large", "FMCG",         30.0),
    ("Wipro Ltd",                 "INE075A01022", "WIPRO",     "3787",  "large", "IT",          113.0),
    ("Tata Motors Ltd",           "INE155A01022", "TATAMOTORS","3456",  "large", "Auto",        209.0),
    ("Power Grid Corp of India",  "INE752E01010", "POWERGRID", "14977", "large", "Utilities",   441.0),
    ("Persistent Systems Ltd",    "INE262H01021", "PERSISTENT","18365", "mid",   "IT",           10.0),
    ("Federal Bank Ltd",          "INE171A01029", "FEDERALBNK","1023",  "mid",   "Financials",  240.0),
    ("Cummins India Ltd",         "INE298A01020", "CUMMINSIND","1901",  "mid",   "Industrials",  14.0),
    ("Voltas Ltd",                "INE226A01021", "VOLTAS",    "3718",  "mid",   "Consumer",     22.0),
    ("Coforge Ltd",               "INE591G01017", "COFORGE",   "11543", "mid",   "IT",            6.0),
    ("Trent Ltd",                 "INE849A01020", "TRENT",     "1964",  "mid",   "Consumer",     16.0),
    ("Kajaria Ceramics Ltd",      "INE217B01036", "KAJARIACER","13293", "small", "Materials",     9.0),
    ("Blue Star Ltd",             "INE472A01039", "BLUESTARCO","8311",  "small", "Consumer",      9.0),
    ("CreditAccess Grameen Ltd",  "INE741K01010", "CREDITACC", "18708", "small", "Financials",    7.0),
    ("Aether Industries Ltd",     "INE0BWX01014", "AETHER",    "5405",  "small", "Materials",     4.0),
]

COLUMNS = ["company_name", "isin", "nse_symbol", "smartapi_token",
           "cap_bucket", "sector", "free_float_shares_cr"]


def load() -> pd.DataFrame:
    return pd.DataFrame(UNIVERSE, columns=COLUMNS)


def nifty50_weights() -> pd.DataFrame:
    """Approximate benchmark weights for large caps — used for active share.

    In production, fetch actual index constituent weights. The active share
    number is only as good as the benchmark you compare against, which is why
    the UI always displays which benchmark was used.
    """
    df = load()
    large = df[df.cap_bucket == "large"].copy()
    large["weight_pct"] = 100.0 / len(large)
    return large[["isin", "company_name", "weight_pct"]]
