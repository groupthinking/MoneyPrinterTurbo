"""One-time YouTube OAuth authorisation helper.

Run this once from your terminal to authorise the app:
    uv run python authorize_youtube.py

A browser window will open. Sign in with the Google account that owns
the YouTube channel you want to upload to. After approval the token is
saved to storage/youtube_token.json and all future uploads are automatic.
"""
import webbrowser
from pathlib import Path

from app.services.youtube import exchange_code, get_auth_url, _TOKEN_PATH


def main() -> None:
    print("Opening browser for YouTube authorisation...")
    auth_url = get_auth_url()
    webbrowser.open(auth_url)
    print(f"\nIf the browser didn't open, visit:\n{auth_url}\n")
    code = input("Paste the authorisation code here: ").strip()
    exchange_code(code)
    print(f"\nDone! Token saved to {_TOKEN_PATH}")
    print("You can now upload videos to YouTube from MoneyPrinterTurbo.")


if __name__ == "__main__":
    main()
