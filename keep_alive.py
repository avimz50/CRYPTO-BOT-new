import os
import json
import time as _time
from flask import Flask, jsonify, send_file, Response
from threading import Thread
import requests as _req

AUDIT_FILE = 'audit_report.json'
_fng_ka    = {'value': None, 'label': None, 'ts': 0}

app = Flask('')

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/')
def home():
    return "I am alive!"

@app.route('/api/audit')
def api_audit():
    """Serve the latest audit report JSON (download or view)."""
    try:
        with open(AUDIT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        response = Response(
            json.dumps(data, indent=2, ensure_ascii=False),
            mimetype='application/json'
        )
        response.headers['Content-Disposition'] = \
            'attachment; filename="trading_audit_report.json"'
        return response
    except FileNotFoundError:
        return jsonify({'error': 'Audit report not yet generated. Runs at 08:00 and 20:00.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# /api/debug removed — exposed cwd and filesystem state to public internet.

@app.route('/api/last_scan')
def api_last_scan():
    """Serve the latest scan analysis report."""
    scan_file = 'artifacts/bot-dashboard/public/last_scan_results.json'
    try:
        with open(scan_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except FileNotFoundError:
        return jsonify({'error': 'No scan report yet — runs after first scan cycle.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fng')
def api_fng():
    """Fear & Greed Index — cached 1h from Alternative.me. Never returns error — fallback to 50/Neutral."""
    now = _time.time()
    if _fng_ka['value'] is None or now - _fng_ka['ts'] > 3600:
        try:
            r = _req.get('https://api.alternative.me/fng/?limit=1', timeout=5)
            d = r.json()['data'][0]
            _fng_ka.update({'value': int(d['value']), 'label': d['value_classification'], 'ts': now})
        except Exception:
            if _fng_ka['value'] is None:
                # Fallback — never block the dashboard
                _fng_ka.update({'value': 50, 'label': 'Neutral', 'ts': now - 3500})
    return jsonify({'value': _fng_ka['value'], 'label': _fng_ka['label']})

def run():
    # Use BOT_PORT so Flask never conflicts with the Express api-server (PORT=8080)
    port = int(os.environ.get('BOT_PORT', 8091))
    # threaded=True: each request gets its own thread — prevents one slow handler
    # from blocking the entire server (critical for /api/sync, /api/tg_hook etc.)
    # use_reloader=False: reloader spawns child processes which breaks daemon threads
    app.run(host='0.0.0.0', port=port, threaded=True, use_reloader=False)

def keep_alive():
    t = Thread(target=run)
    t.start()
