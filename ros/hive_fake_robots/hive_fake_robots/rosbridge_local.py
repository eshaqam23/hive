"""The stock rosbridge_websocket server, except that it refuses browser handshakes.

Browsers send an Origin header on every WebSocket handshake and no CORS check applies, so
without this any web page the user opens could drive the fake fleet through 127.0.0.1:9090.
rosbridge_server 2.7.1 accepts every Origin (RosbridgeWebSocket.check_origin returns True)
and has no parameter for it. Tornado consults check_origin only when the handshake carries an
Origin, so @corebrum/core and other non-browser clients, which send none, are unaffected.
"""

import os
import runpy

from ament_index_python.packages import get_package_prefix
from rosbridge_server import RosbridgeWebSocket


def _refuse_any_origin(self, origin: str) -> bool:
    return False


def main():
    RosbridgeWebSocket.check_origin = _refuse_any_origin
    print("[hive] rosbridge: refusing every handshake that carries an Origin header", flush=True)
    script = os.path.join(get_package_prefix("rosbridge_server"), "lib", "rosbridge_server",
                          "rosbridge_websocket")
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
