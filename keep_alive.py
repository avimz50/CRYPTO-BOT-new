import os
import json
from flask import Flask, jsonify, send_file, Response
from threading import Thread

AUDIT_FILE = 'audit_report.json'

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

def run():
    port = int(os.environ.get('PORT', 8090))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
