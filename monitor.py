import base64
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = 'https://www.nintendo.com/us/store/products/the-legend-of-zelda-ocarina-of-time-playable-ocarina-129683/'


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    if Path('alert-reserved.json').exists():
        print('Alert already reserved. Check Twilio delivery logs before resetting.')
        return
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale='en-US')
        page.goto(URL, wait_until='domcontentloaded', timeout=60000)
        main = page.get_by_role('main')
        main.get_by_role('heading', name='The Legend of Zelda™: Ocarina of Time Playable Ocarina', exact=True).wait_for()
        # Missing or stuck purchase controls are errors, never stock signals.
        buy = main.get_by_role('button', name=re.compile(r'^Add to cart$', re.I))
        sold = main.get_by_role('button', name=re.compile(r'^(Sold out|Out of stock)$', re.I))
        import time
        deadline = time.monotonic() + 60
        available = False
        while time.monotonic() < deadline:
            if buy.count() == 1 and buy.is_visible() and buy.is_enabled():
                available = True
                break
            if sold.count() == 1 and sold.is_visible():
                print('Nintendo reports: Sold out / Out of stock. No SMS sent.')
                browser.close()
                return
            time.sleep(2)
        browser.close()
        if not available:
            raise RuntimeError('Could not resolve stock controls. Review Nintendo page; no SMS sent.')

    required = ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TO_PHONE', 'FROM_PHONE', 'GH_TOKEN', 'GITHUB_REPOSITORY']
    if any(not os.environ.get(k) for k in required):
        raise RuntimeError('Missing required GitHub secrets or environment variables')
    # Reserve durably BEFORE sending. An uncertain send cannot cause repeated texts.
    payload = json.dumps({'message': 'Reserve one-time stock alert', 'content': base64.b64encode(b'{"reserved":true}\n').decode()}).encode()
    req = urllib.request.Request(
        'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/alert-reserved.json',
        data=payload, method='PUT', headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json', 'User-Agent': 'ocarina-monitor'})
    with urllib.request.urlopen(req, timeout=30) as response:
        json.load(response)
    auth = base64.b64encode((os.environ['TWILIO_ACCOUNT_SID'] + ':' + os.environ['TWILIO_AUTH_TOKEN']).encode()).decode()
    result = request('https://api.twilio.com/2010-04-01/Accounts/' + os.environ['TWILIO_ACCOUNT_SID'] + '/Messages.json',
        urllib.parse.urlencode({'To': os.environ['TO_PHONE'], 'From': os.environ['FROM_PHONE'], 'Body': 'sms_appointment_reminders'}).encode(),
        {'Authorization': 'Basic ' + auth})
    if result.get('status') in ('failed', 'undelivered'):
        raise RuntimeError('Twilio rejected the message; inspect Twilio logs')
    print('Twilio accepted the stock alert. Confirm delivery in Twilio Messaging logs.')


if __name__ == '__main__':
    main()
