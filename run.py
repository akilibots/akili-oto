import datetime
import requests
import urllib
import websocket
import threading
import time
import os

import pyjson5

from dydx3 import Client
from dydx3.constants import *
from dydx3.helpers.request_helpers import generate_now_iso

from config import config


# Global Vars
xchange = None
signature = None
signature_time = None
account = None

# Constants
GOOD_TILL = 31536000


def log(msg):
    def _log(_msg):
        conf = config()
        _msg = conf['main']['name'] + ':' + _msg
        print(datetime.datetime.now().isoformat(), _msg)

        if conf['telegram']['chatid'] == '' or conf['telegram']['bottoken'] == '':
            return

        params = {
            'chat_id': conf['telegram']['chatid'],
            'text': _msg
        }
        payload_str = urllib.parse.urlencode(params, safe='@')
        requests.get(
            'https://api.telegram.org/bot' +
            conf['telegram']['bottoken'] + '/sendMessage',
            params=payload_str
        )
    threading.Thread(target=_log, args=[msg]).start()

def save_state():
    # Save state of bot so that it can resume in case it dies for some reason (which it does often!)
    global order_id
    global orders

    save_data = {
        'order_id':order_id,
        'orders':orders,
    }

    with open("data/state.json", "wt") as f:
        pyjson5.encode_io(save_data, f, supply_bytes=False)


def load_state():
    global order_id
    global orders

    log('Check for saved state.')
    if not os.path.isfile('data/state.json'):
        log('No state saved. Start new.')
        return False

    with open("data/state.json", "rt") as f:
        load_data = pyjson5.decode_io(f)

    order_id = load_data['order_id']
    orders = load_data['orders'].copy()

    log('State loaded.')
    return True


def place_order(side, size, price):
    global xchange
    global account
    conf = config()

    order = xchange.private.create_order(
        position_id=account['positionId'],
        market=conf['main']['market'],
        side=side,
        order_type=ORDER_TYPE_LIMIT,
        post_only=True,
        size=str(size),
        price=str(price),
        limit_fee='0.1',
        expiration_epoch_seconds=int(time.time()) + GOOD_TILL,
    ).data['order']

    log(f'{side} order size {size} placed @ {price}')
    return order


def ws_open(ws):
    global signature
    global signature_time

    # Subscribe to order book updates
    log('Subscribing to order changes')
    ws.send(pyjson5.encode({
        'type': 'subscribe',
        'channel': 'v3_accounts',
        'accountNumber': '0',
        'apiKey': xchange.api_key_credentials['key'],
        'passphrase': xchange.api_key_credentials['passphrase'],
        'timestamp': signature_time,
        'signature': signature,
    }))


def ws_message(ws, message):
    global orders
    global order_id

    conf = config()
   
    # Check only for order messages
    message = pyjson5.decode(message)
    if message['type'] != 'channel_data':
        # Not an order book update
        return

    if len(message['contents']['orders']) == 0:
        # No orders to process
        return

    print(orders)
    for i in range(len(orders)):
        print(i)
        for exchange_order in message['contents']['orders']:
            if orders[i]['exchange_order']['id'] == exchange_order['id']:
                if exchange_order['status'] == 'CANCELED':
                    # Reinstate ALL cancelled orders (CANCELED is mis-spelt smh Americans!!)
                    log(f'Recreate order 😡 {exchange_order["side"]} order at {exchange_order["price"]}')
                    orders[i]['exchange_order'] = place_order(exchange_order['side'], exchange_order['size'], exchange_order['price'])
                    # Save replacement order info
                    save_state()

                if exchange_order['status'] == 'FILLED':
                    # Cancel all other orders
                    for j in range(len(orders)):
                        try:
                            xchange.private.cancel_order(orders[j]['exchange_order']['id'])
                            log(f'Cancel {orders[j]["exchange_order"]["side"]} order at {orders[j]["exchange_order"]["price"]}')
                        except:
                            pass

                    order_id = orders[i]['config_order']['next']
                    log(f'Order ID {order_id}')
                    if order_id == -1:
                        log('ID -1 exit')
                        ws.close()
                        return

                    orders = []
                    for order_creator in conf['orders']:
                        if order_creator['id'] == order_id:
                            new_order = place_order(ORDER_SIDE_BUY if order_creator['side']=='buy' else ORDER_SIDE_SELL,order_creator['size'],order_creator['price'])
                            orders.append(orders.append({"exchange_order":new_order, "config_order":order_creator}))
                    # Save new state
                    save_state()


def ws_close(ws, p2, p3):
    log('Asked to stop some reason')
    save_state()


def on_ping(ws, message):
    global account
    global xchange

    # To keep connection API active
    account = xchange.private.get_account().data['account']


def main():
    global orders
    global order_id
    global signature_time
    global signature
    global account
    global xchange
    
    startTime = datetime.datetime.now()

    # Load configuration
    conf = config()

    log(f'Start {startTime.isoformat()}')

    log('DEX connect.')
    xchange = Client(
        network_id=NETWORK_ID_MAINNET,
        host=API_HOST_MAINNET,
        api_key_credentials={
            'key': conf['dydx']['APIkey'],
            'secret': conf['dydx']['APIsecret'],
            'passphrase': conf['dydx']['APIpassphrase'],
        },
        stark_private_key=conf['dydx']['stark_private_key'],
        default_ethereum_address=conf['dydx']['default_ethereum_address'],
    )

    signature_time = generate_now_iso()
    signature = xchange.private.sign(
        request_path='/ws/accounts',
        method='GET',
        iso_timestamp=signature_time,
        data={},
    )

    account = xchange.private.get_account().data['account']

    if not load_state():

        orders = []
        order_id = 0

        log(f'Order ID {order_id}')

        for order_creator in conf['orders']:
            if order_creator['id'] == order_id:
                new_order = place_order(ORDER_SIDE_BUY if order_creator['side']=='buy' else ORDER_SIDE_SELL,order_creator['size'],order_creator['price'])
                orders.append({"exchange_order":new_order, "config_order":order_creator})
        # Save new state
        save_state()

    log('Starting bot loop')
    # websocket.enableTrace(True)
    wsapp = websocket.WebSocketApp(
        WS_HOST_MAINNET,
        on_open=ws_open,
        on_message=ws_message,
        on_close=ws_close,
        on_ping=on_ping
    )
    wsapp.run_forever(ping_interval=60, ping_timeout=20)


if __name__ == "__main__":
    main()