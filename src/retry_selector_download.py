"""Retry only transport failures in the unchanged frozen serial downloader."""
import json
from pathlib import Path
import socket
import ssl
import time
import urllib.error
from prepare_selector_holdout24 import download

if __name__ == '__main__':
    for attempt in range(10):
        try:
            result = download(Path('.').resolve())
            print(json.dumps({'complete': result['complete'], 'files': result['files']}), flush=True)
            break
        except (urllib.error.URLError, socket.timeout, ssl.SSLError) as error:
            if attempt == 9:
                raise
            print(json.dumps({'transport_retry': attempt + 1, 'error_type': type(error).__name__}), flush=True)
            time.sleep(min(5.0 * (attempt + 1), 30.0))
