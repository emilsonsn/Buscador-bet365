import os
from datetime import datetime
from pathlib import Path
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from src.logging_service import get_logger

LOGGER = get_logger()

MESES = {1:"JAN", 2:"FEV", 3:"MAR", 4:"ABR", 5:"MAI", 6:"JUN", 7:"JUL", 8:"AGO", 9:"SET", 10:"OUT", 11:"NOV", 12:"DEZ"}


class SpreadsheetService:
    def __init__(self, credentials_path=None, spreadsheet_id=None):
        self.credentials_path = credentials_path or os.getenv("GOOGLE_CREDENTIALS_PATH")
        self.spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SPREADSHEET_ID")

    def connect(self):
        return connect_spreadsheet(self.credentials_path, self.spreadsheet_id)

    def current_sheet(self, spreadsheet=None):
        return current_month_sheet(spreadsheet or self.connect())


def connect_spreadsheet(credentials_path=None, spreadsheet_id=None):
    credentials_path = Path(credentials_path or Path(__file__).resolve().parent.parent / "credentials.json").expanduser()
    spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID não foi definido no .env.")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        return gspread.authorize(creds).open_by_key(spreadsheet_id)
    except Exception:
        LOGGER.exception("Falha ao conectar à planilha Google.")
        raise


def current_month_sheet(spreadsheet):
    return spreadsheet.worksheet(MESES[datetime.now().month])
