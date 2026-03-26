import os
from flask import Flask, jsonify
from threading import Thread

app = Flask('')

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/')
def home():
    return "I am alive!"

def run():
    port = int(os.environ.get('PORT', 8090))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
