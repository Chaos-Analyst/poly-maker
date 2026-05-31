import asyncio                      # Asynchronous I/O
import json                        # JSON handling
import websockets                  # WebSocket client
import traceback                   # Exception handling

from poly_data.data_processing import process_data, process_user_data
import poly_data.global_state as global_state


async def _keepalive(websocket, interval=10):
    """Send Polymarket's app-level keepalive ("PING") every ``interval`` seconds.

    Polymarket's CLOB websocket closes otherwise-idle connections unless it periodically receives
    the literal text "PING"; it replies "PONG" (skipped by the recv loops). This runs as a task
    alongside the recv loop and returns quietly once the connection is closing.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            await websocket.send("PING")
    except Exception:
        # Connection is closing/closed -- the recv loop's handler drives the reconnect.
        return


async def connect_market_websocket(chunk):
    """
    Connect to Polymarket's market WebSocket API and process market updates.
    
    This function:
    1. Establishes a WebSocket connection to the Polymarket API
    2. Subscribes to updates for a specified list of market tokens
    3. Processes incoming order book and price updates
    
    Args:
        chunk (list): List of token IDs to subscribe to
        
    Notes:
        If the connection is lost, the function will exit and the main loop will
        attempt to reconnect after a short delay.
    """
    subscribed = list(chunk)
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    async with websockets.connect(uri, ping_interval=5, ping_timeout=None) as websocket:
        # Prepare and send subscription message
        sub_message = {"assets_ids": subscribed}
        await websocket.send(json.dumps(sub_message))

        print("\n")
        print(f"Sent market subscription message: {sub_message}")

        # App-level keepalive so Polymarket doesn't drop an idle connection.
        keepalive_task = asyncio.create_task(_keepalive(websocket))

        try:
            # Process incoming market data indefinitely
            while True:
                # If the traded token set changed (the updater discovered new markets, or
                # populated the table after we connected), return so the main loop reconnects
                # and resubscribes with the current set.
                if set(global_state.all_tokens) != set(subscribed):
                    print("Market token set changed; reconnecting to resubscribe")
                    return

                try:
                    # Time out periodically so we re-check the token set even when no book
                    # updates arrive (e.g. an empty or stale subscription).
                    message = await asyncio.wait_for(websocket.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue

                # Keepalive replies ("PONG", or a server-sent "PING") aren't JSON -- skip them.
                if message in ("PONG", "PING"):
                    continue

                json_data = json.loads(message)
                # Process order book updates and trigger trading as needed
                process_data(json_data)
        except websockets.ConnectionClosed:
            print("Connection closed in market websocket")
            print(traceback.format_exc())
        except Exception as e:
            print(f"Exception in market websocket: {e}")
            print(traceback.format_exc())
        finally:
            keepalive_task.cancel()
            # Brief delay before attempting to reconnect
            await asyncio.sleep(5)

async def connect_user_websocket():
    """
    Connect to Polymarket's user WebSocket API and process order/trade updates.
    
    This function:
    1. Establishes a WebSocket connection to the Polymarket user API
    2. Authenticates using API credentials
    3. Processes incoming order and trade updates for the user
    
    Notes:
        If the connection is lost, the function will exit and the main loop will
        attempt to reconnect after a short delay.
    """
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    async with websockets.connect(uri, ping_interval=5, ping_timeout=None) as websocket:
        # Prepare authentication message with API credentials
        message = {
            "type": "user",
            "auth": {
                "apiKey": global_state.client.client.creds.api_key, 
                "secret": global_state.client.client.creds.api_secret,  
                "passphrase": global_state.client.client.creds.api_passphrase
            }
        }

        # Send authentication message
        await websocket.send(json.dumps(message))

        print("\n")
        print(f"Sent user subscription message")

        # App-level keepalive so Polymarket doesn't drop an idle connection.
        keepalive_task = asyncio.create_task(_keepalive(websocket))

        try:
            # Process incoming user data indefinitely
            while True:
                message = await websocket.recv()
                # Keepalive replies ("PONG", or a server-sent "PING") aren't JSON -- skip them.
                if message in ("PONG", "PING"):
                    continue
                json_data = json.loads(message)
                # Process trade and order updates
                process_user_data(json_data)
        except websockets.ConnectionClosed:
            print("Connection closed in user websocket")
            print(traceback.format_exc())
        except Exception as e:
            print(f"Exception in user websocket: {e}")
            print(traceback.format_exc())
        finally:
            keepalive_task.cancel()
            # Brief delay before attempting to reconnect
            await asyncio.sleep(5)