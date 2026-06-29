"""手動將股票清單寫入 Firestore system/stock_list。

用途：
  - 週末/假日 TWSE API 無日線資料，無法透過 14:30 排程自動填入時，手動補充
  - 首次部署後快速初始化，不用等到下一個交易日 14:30

執行方式（在專案根目錄）：
  poetry run python scripts/populate_stock_list.py

需要：
  - .env 含有效的 GOOGLE_APPLICATION_CREDENTIALS、FIRESTORE_PROJECT_ID
  - .personal/secrets/firestore-service-account.json 存在
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import httpx
from google.cloud import firestore
from google.oauth2 import service_account

from app.config import get_settings
from app.models.schemas import StockQuote
from app.routers.tick import _save_stock_list_to_firestore
from app.services.market_data_client import fetch_stock_list

SERVICE_ACCOUNT_FILE = Path(__file__).parent.parent / ".personal" / "secrets" / "firestore-service-account.json"


def get_local_firestore_client() -> firestore.Client:
    credentials = service_account.Credentials.from_service_account_file(str(SERVICE_ACCOUNT_FILE))
    return firestore.Client(project=get_settings().firestore_project_id, credentials=credentials)

TWSE_LISTING_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_LISTING_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company_information"


def fetch_twse_listing_fallback(client: httpx.Client) -> list[StockQuote]:
    """備用：TWSE 公司基本資料（不含收盤價），週末/假日也可使用。"""
    response = client.get(TWSE_LISTING_URL, timeout=10)
    response.raise_for_status()
    return [
        StockQuote(code=row["公司代號"], name=row["公司簡稱"], close=None)
        for row in response.json()
        if row.get("公司代號") and row.get("公司簡稱")
    ]


def fetch_tpex_listing_fallback(client: httpx.Client) -> list[StockQuote]:
    """備用：TPEx 公司基本資料（不含收盤價），週末/假日也可使用。"""
    response = client.get(TPEX_LISTING_URL, timeout=10)
    response.raise_for_status()
    return [
        StockQuote(code=row["SecuritiesCompanyCode"], name=row["CompanyName"], close=None)
        for row in response.json()
        if row.get("SecuritiesCompanyCode") and row.get("CompanyName")
    ]


def main() -> None:
    # --- 第一優先：正常路徑（含收盤價）---
    print("嘗試正常路徑（含收盤價）...")
    try:
        stock_list = fetch_stock_list()
    except Exception as e:
        print(f"  正常路徑失敗：{e}")
        stock_list = []

    if stock_list:
        print(f"  ✅ 取得 {len(stock_list)} 支股票（含收盤價）")
    else:
        # --- 備用路徑：不含收盤價的公司清單（週末/假日可用）---
        print("  正常路徑回傳空清單（可能是非交易日）")
        print("嘗試備用路徑（不含收盤價）...")
        stock_list = []
        with httpx.Client() as client:
            for name, fn in [("TWSE", fetch_twse_listing_fallback), ("TPEx", fetch_tpex_listing_fallback)]:
                try:
                    result = fn(client)
                    print(f"  {name}：取得 {len(result)} 支")
                    stock_list.extend(result)
                except Exception as e:
                    print(f"  {name} 備用路徑失敗：{e}")

    if not stock_list:
        print("❌ 所有路徑都無法取得資料，請稍後再試。")
        sys.exit(1)

    # --- 寫入 Firestore ---
    print(f"\n寫入 Firestore system/stock_list（共 {len(stock_list)} 支）...")
    try:
        client = get_local_firestore_client()
        _save_stock_list_to_firestore(stock_list, firestore_client=client)
        print("✅ 完成！現在可以在 LINE 測試記帳功能。")
        if stock_list[0].close is None:
            print("   ⚠️  本次使用備用路徑，收盤價為空；查詢損益時數字會是 0。")
            print("      待下一個交易日 14:30 排程跑完，收盤價就會自動更新。")
    except Exception as e:
        print(f"❌ 寫入 Firestore 失敗：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
