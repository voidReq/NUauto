import gspread

import config


def main():
    gc = gspread.oauth(
        scopes=config.READ_SCOPES,
        credentials_filename=config.find_client_json(),
        authorized_user_filename=config.TOKEN_PATH,
    )
    config.lock_token()
    sh = gc.open_by_key(config.SHEET_ID)
    ws = sh.sheet1
    print(f"Connected. Sheet: {sh.title!r}, first tab: {ws.title!r}, rows with data: {len(ws.get_all_values())}")


if __name__ == "__main__":
    main()
